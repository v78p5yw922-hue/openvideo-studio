"""FastAPI surface for the studio.

Rules baked in:
* No GPU work starts without `POST /confirm` and a plan whose id still matches.
* The executor is off unless STUDIO_ALLOW_RENDER=1 (set deliberately by a human).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import doctor
from ..core import db, service, assets, model_status, auth
from ..exec import render_adapter, runner

WEB = Path(__file__).resolve().parent.parent / "web"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # start the executor only when a real server runs (uvicorn triggers lifespan);
    # tests import the app without starting background threads
    db.init_db()
    auth.ensure_default_admin()
    orphans = service.fail_orphan_running()
    # 服务重启会留下没人认领的 render_drama 子进程，它们继续占着 GPU
    runner.kill_orphan_renderers()
    if orphans:
        print(f"[startup] 清理了 {orphans} 个上次遗留的 running 任务")
    runner.start()
    # 启动体检：只报告，不阻止启动（渲染前还有一次同样的检查）
    if os.environ.get("OPEN_VIDEO_SKIP_DOCTOR", "") != "1":
        try:
            _rep = doctor.report()
            if not _rep["ok"]:
                print("[doctor] " + _rep["summary"])
                for _c in _rep["checks"]:
                    if _c["status"] != "ok":
                        print(f"[doctor] {_c['status']}: {_c['label']} — {_c['detail']}")
        except Exception as _e:  # noqa: BLE001
            print(f"[doctor] 体检失败（不影响启动）：{_e}")
    yield



def _html(path, media_type: str = "text/html; charset=utf-8"):
    """HTML 必须禁用缓存：否则改了前端、浏览器还拿旧版，表现为"刷新后空白/功能缺失"。"""
    resp = FileResponse(path, media_type=media_type)
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


app = FastAPI(title="OpenVideo Studio", version="0.1.0", lifespan=lifespan)
db.init_db()
auth.ensure_default_admin()


# ------------------------------------------------------------------- 登录守卫

PUBLIC_API = {"/api/auth/login", "/api/health", "/api/selftest", "/api/health/deps"}


def _auth_enabled() -> bool:
    """STUDIO_AUTH=0 只用于自动化测试；生产默认必须登录。"""
    return os.environ.get("STUDIO_AUTH", "1") != "0"


def _token_of(request: Request) -> str:
    hdr = request.headers.get("authorization") or ""
    if hdr.lower().startswith("bearer "):
        return hdr[7:].strip()
    cookie = request.cookies.get("ov_token")
    if cookie:
        return cookie
    return request.query_params.get("token", "")


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if _auth_enabled() and path.startswith("/api/") and path not in PUBLIC_API:
        user = auth.resolve_session(_token_of(request))
        if not user:
            return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期"})
        request.state.user = user
        if path.startswith("/api/users") and user["role"] != auth.ROLE_ADMIN:
            return JSONResponse(status_code=403, content={"detail": "需要管理员权限"})
    return await call_next(request)


# ------------------------------------------------------------------- models

class ProjectIn(BaseModel):
    id: str
    title: str
    style: str = ""


class ShotsIn(BaseModel):
    shots: list[dict]


class SceneIn(BaseModel):
    scene_id: str
    name: str = ""
    camera: str = ""
    props: str = ""
    light: str = ""
    plate_path: str = ""


class CharacterIn(BaseModel):
    character_id: str
    name: str
    description: str = ""
    refs: list[str] = []


class PlanIn(BaseModel):
    shot_ids: list[str] | None = None
    draft: bool = False
    prompt_mode: str = ""          # compile（H3 三字段）/ concat（旧拼接）
    force: bool = False            # 已通过的镜头也重渲（A/B 对比用）


class ChainRefIn(BaseModel):
    enabled: bool = True


class CompilePreviewIn(BaseModel):
    prompt: str


class RenameIn(BaseModel):
    title: str


class ConfirmIn(BaseModel):
    plan_id: str
    draft: bool = False
    pause_after_one: bool = False
    prompt_mode: str = ""          # 空 = 用服务端默认（compile）
    unet: str = ""                 # fl2va（默认）/ ref2va（参考图专用）
    retries: int = 0               # 失败自动重试次数（夜间批渲用 2）


class ReviewIn(BaseModel):
    shot_id: str
    verdict: str
    reason: str = ""


# ------------------------------------------------------------------- routes

class LoginIn(BaseModel):
    username: str
    password: str
    remember: bool = True


class ProfileIn(BaseModel):
    display_name: str | None = None
    phone: str | None = None
    password: str | None = None
    old_password: str | None = None


class UserIn(BaseModel):
    username: str
    password: str
    display_name: str = ""
    phone: str = ""
    role: str = "member"


class UserPatch(BaseModel):
    display_name: str | None = None
    phone: str | None = None
    role: str | None = None
    active: bool | None = None
    password: str | None = None


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response):
    user = auth.authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(401, "用户名或密码不正确")
    days = 365 if body.remember else 1
    sess = auth.create_session(user["id"], days=days)
    response.set_cookie("ov_token", sess["token"], max_age=days * 86400,
                        httponly=True, samesite="lax")
    return {"token": sess["token"], "expires_at": sess["expires_at"], "user": user}


@app.post("/api/auth/logout")
def logout(request: Request):
    auth.delete_session(_token_of(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("ov_token")
    return resp


@app.get("/api/auth/me")
def me(request: Request):
    user = dict(request.state.user or {})
    f = db.DATA_DIR / "avatars" / f"{user.get('id')}.png"
    if f.is_file():
        user["avatar_url"] = f"/assets/avatar/{user['id']}?v={int(f.stat().st_mtime)}"
    return {"user": user}


@app.put("/api/auth/me")
def update_me(body: ProfileIn, request: Request):
    user = request.state.user
    if body.password:
        if not body.old_password or not auth.check_password(user["id"], body.old_password):
            raise HTTPException(400, "当前密码不正确")
    try:
        updated = auth.update_user(
            user["id"], display_name=body.display_name, phone=body.phone,
            password=body.password or None,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"user": updated}


@app.post("/api/auth/me/avatar")
async def upload_avatar(request: Request):
    """上传头像（当前登录用户），返回可直接引用的 URL。"""
    user = request.state.user
    body = await request.body()
    if not body:
        raise HTTPException(400, "空文件")
    if len(body) > 8 * 1024 * 1024:
        raise HTTPException(400, "头像不能超过 8 MB")
    d = db.DATA_DIR / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / f"{user['id']}.png"
    dst.write_bytes(body)
    return {"ok": True, "url": f"/assets/avatar/{user['id']}?v={int(dst.stat().st_mtime)}"}


@app.get("/assets/avatar/{uid}")
def get_avatar(uid: str):
    from fastapi.responses import FileResponse as _FR

    f = db.DATA_DIR / "avatars" / f"{uid}.png"
    if not f.is_file():
        raise HTTPException(404, "没有头像")
    return _FR(f, media_type="image/png")


@app.get("/api/users")
def list_users():
    return {"users": auth.list_users()}


@app.post("/api/users")
def create_user(body: UserIn):
    try:
        return {"user": auth.create_user(
            body.username, body.password, body.display_name, body.phone, body.role)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.put("/api/users/{uid}")
def update_user(uid: str, body: UserPatch):
    try:
        return {"user": auth.update_user(
            uid, display_name=body.display_name, phone=body.phone,
            role=body.role, active=body.active, password=body.password or None)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/users/{uid}/reset-password")
def reset_user_password(uid: str, body: dict):
    password = str(body.get("password") or "")
    if len(password) < 6:
        raise HTTPException(400, "密码至少 6 位")
    try:
        return {"user": auth.update_user(uid, password=password)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.delete("/api/users/{uid}")
def delete_user(uid: str, request: Request):
    if uid == request.state.user["id"]:
        raise HTTPException(400, "不能删除自己")
    try:
        auth.delete_user(uid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


@app.get("/api/projects")
def projects(include_archived: int = 0):
    return {"projects": service.list_projects(bool(include_archived))}


@app.get("/api/groups")
def groups():
    return {"groups": service.list_groups()}


@app.post("/api/groups")
def create_group(body: dict):
    try:
        return service.ensure_group(str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.patch("/api/groups/{gid}")
def patch_group(gid: str, body: dict):
    try:
        return service.rename_group(gid, str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/groups/{gid}")
def remove_group(gid: str):
    return service.delete_group(gid)


@app.post("/api/projects/batch")
def batch_project_ops(body: dict):
    try:
        return service.batch_projects(
            [str(x) for x in (body.get("ids") or [])],
            str(body.get("action") or ""),
            str(body.get("group_id") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/order")
def reorder_projects(body: dict):
    return service.reorder_projects([str(x) for x in (body.get("ids") or [])])


@app.post("/api/projects/{pid}/group")
def set_group(pid: str, body: dict):
    return service.set_project_group(pid, str(body.get("group_id") or ""))


@app.post("/api/projects/{pid}/archive")
def set_archived(pid: str, body: dict):
    return service.set_project_archived(pid, bool(body.get("archived")))


@app.post("/api/projects")
def create_project(body: ProjectIn):
    if service.get_project(body.id):
        raise HTTPException(409, "项目已存在")
    return service.create_project(body.id, body.title, body.style)


@app.get("/api/projects/{pid}/settings")
def get_project_settings(pid: str):
    return service.project_settings(pid)


class ProjectSettingsIn(BaseModel):
    style: str | None = None
    width: int | None = None
    height: int | None = None
    max_duration: float | None = None
    glossary_extra: list[str] | None = None


@app.put("/api/projects/{pid}/settings")
def put_project_settings(pid: str, body: ProjectSettingsIn):
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    return service.update_project_settings(pid, patch)


@app.get("/api/projects/{pid}")
def project_detail(pid: str):
    p = service.get_project(pid)
    if not p:
        raise HTTPException(404, "项目不存在")
    shots = service.recompute_fingerprints(pid)
    used: dict[str, list[str]] = {}
    for sh in shots:
        for cid in sh["cast"]:
            used.setdefault(cid, []).append(sh["shot_id"])
    characters = [{**c, "used_in": used.get(c["character_id"], [])}
                  for c in service.list_characters(pid)]
    out = []
    ordered_ids = [s["shot_id"] for s in shots]
    for pos, sh in enumerate(shots):
        st = service.shot_state(pid, sh)
        prog = runner.progress_of(st["job"]["id"]) if st.get("job") else None
        chain_from = None
        if str(sh.get("transition")) == "chain" and pos > 0:
            prev_id = ordered_ids[pos - 1]
            prev_take = service.latest_take(pid, prev_id)
            chain_from = {
                "shot_id": prev_id,
                "version": (prev_take or {}).get("version"),
                "ready": bool(prev_take),
            }
        elif str(sh.get("transition")) == "chain":
            chain_from = {"shot_id": None, "version": None, "ready": False}
        out.append({**sh, "state": st["state"], "take": st["take"],
                    "review": st["review"], "preview": st["preview"],
                    "job": st["job"], "preview_job": st.get("preview_job"),
                    "last_job": st.get("last_job"), "chain_from": chain_from,
                    "progress": prog})
    return {"project": p, "shots": out, "scenes": service.list_scenes(pid),
            "characters": characters, "counts": _counts(out)}


@app.put("/api/projects/{pid}/shots")
def save_shots(pid: str, body: ShotsIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return {"shots": service.save_shots(pid, body.shots)}


@app.post("/api/projects/{pid}/scenes")
def save_scene(pid: str, body: SceneIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return service.upsert_scene(pid, body.scene_id, name=body.name, camera=body.camera,
                                props=body.props, light=body.light, plate_path=body.plate_path)


@app.post("/api/projects/{pid}/scenes/{scene_id}/plate")
async def upload_plate(pid: str, scene_id: str, file: UploadFile = File(...),
                       force: bool = False):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    # write a NEW file (never overwrite in place, or the old plate is gone before
    # it can be archived) and let the service decide about the lock
    stamp = db.now().replace(":", "").replace("-", "")
    dest = db.DATA_DIR / "projects" / pid / "scenes" / f"{scene_id}_{stamp}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    scene = service.get_scene(pid, scene_id) or {}
    try:
        return service.upsert_scene(pid, scene_id, name=scene.get("name", ""),
                                    camera=scene.get("camera", ""), props=scene.get("props", ""),
                                    light=scene.get("light", ""), plate_path=str(dest),
                                    force=force)
    except service.LockedError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from e


@app.delete("/api/projects/{pid}/scenes/{sid}")
def delete_scene_api(pid: str, sid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    if not service.delete_scene(pid, sid):
        raise HTTPException(404, "场景不存在")
    return {"ok": True}


@app.post("/api/projects/{pid}/scenes/{sid}/plates")
async def add_scene_plate(pid: str, sid: str,
                          files: list[UploadFile] = File(...)):
    """再传一张同场景不同机位的参考图（多角度让空间不至于被压平）。"""
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    stamp = db.now().replace(":", "").replace("-", "")
    dest_dir = db.DATA_DIR / "projects" / pid / "scenes"
    dest_dir.mkdir(parents=True, exist_ok=True)
    scene = None
    added = 0
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            continue
        suffix = Path(f.filename or "").suffix.lower() or ".jpg"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            suffix = ".jpg"
        dest = dest_dir / f"{sid}_alt_{stamp}_{i}{suffix}"
        dest.write_bytes(data)
        try:
            scene = service.add_scene_plate(pid, sid, str(dest))
            added += 1
        except ValueError as e:
            dest.unlink(missing_ok=True)
            raise HTTPException(404, str(e)) from e
    if not added:
        raise HTTPException(400, "空文件")
    return {**scene, "added": added}


@app.delete("/api/projects/{pid}/scenes/{sid}/plates/{index}")
def drop_scene_plate(pid: str, sid: str, index: int):
    try:
        return service.remove_scene_plate(pid, sid, index)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/scenes/{sid}/lock")
def lock_scene(pid: str, sid: str, body: dict):
    try:
        return service.set_scene_lock(pid, sid, bool(body.get("locked", True)))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/projects/{pid}/scenes/{sid}/versions")
def scene_versions(pid: str, sid: str):
    return {"versions": service.list_versions("scene", sid)}


@app.post("/api/projects/{pid}/scenes/{sid}/restore")
def restore_scene(pid: str, sid: str, body: dict):
    try:
        return service.restore_scene_plate(pid, sid, int(body.get("version") or 0))
    except (ValueError, service.LockedError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/images/inpaint")
async def inpaint_image(request: Request):
    """框选擦除（去水印）：用 OpenCV inpaint，**不改变分辨率**。

    表单字段：
      - file: 图片文件
      - boxes: JSON 字符串，元素 {x, y, w, h}，坐标是**图像像素**坐标
    返回擦除后的 PNG（image/png）。
    """
    try:
        import cv2
        import numpy as np
    except ImportError as e:
        raise HTTPException(500, f"opencv 不可用：{e}") from e

    form = await request.form()
    upload = form.get("file")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(400, "缺少 file")
    raw = await upload.read()
    if not raw:
        raise HTTPException(400, "空文件")

    boxes = json.loads(form.get("boxes") or "[]")
    arr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "无法解码图片")
    h, w = img.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    pad = 2  # 选区外扩 2px，避免边缘残留水印
    for b in boxes:
        try:
            x1 = max(0, min(w, int(b.get("x", 0))))
            y1 = max(0, min(h, int(b.get("y", 0))))
            x2 = max(0, min(w, int(b.get("x", 0)) + int(b.get("w", 0))))
            y2 = max(0, min(h, int(b.get("y", 0)) + int(b.get("h", 0))))
        except (TypeError, ValueError):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        cv2.rectangle(mask,
                      (max(0, x1 - pad), max(0, y1 - pad)),
                      (min(w, x2 + pad), min(h, y2 + pad)),
                      255, -1)
    if int(mask.sum()) == 0:
        raise HTTPException(400, "没有有效选区")

    out = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
    ok, buf = cv2.imencode(".png", out)
    if not ok:
        raise HTTPException(500, "编码失败")
    return Response(content=buf.tobytes(), media_type="image/png")


@app.post("/api/characters/{cid}/ref")
async def upload_ref(cid: str, file: UploadFile = File(...), force: bool = False):
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    stamp = db.now().replace(":", "").replace("-", "")
    dest = db.DATA_DIR / "characters" / f"{cid}_{stamp}.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid, {"name": cid, "description": "", "refs": [], "locked": 0})
    refs = [r for r in cur.get("refs", []) if Path(r).name != dest.name]
    refs.insert(0, str(dest))
    try:
        return service.upsert_character(cid, cur.get("name", cid), cur.get("description", ""),
                                        refs, force=force)
    except service.LockedError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from e


@app.post("/api/characters/{cid}/refs")
async def upload_refs(cid: str, files: list[UploadFile] = File(...),
                      force: bool = False, primary_index: int = -1):
    """一次上传多张参考图（角色图集）。

    - 按上传顺序**追加**到已有列表末尾（不再像旧接口那样插到最前面打乱主图）
    - `primary_index >= 0` 指定第几张当主参考图（渲染时优先用它）
    """
    if not files:
        raise HTTPException(400, "没有文件")
    stamp = db.now().replace(":", "").replace("-", "")
    dest_dir = db.DATA_DIR / "characters"
    dest_dir.mkdir(parents=True, exist_ok=True)

    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid, {"name": cid, "description": "", "refs": [], "locked": 0})
    refs = list(cur.get("refs", []))
    added: list[str] = []
    for i, f in enumerate(files):
        data = await f.read()
        if not data:
            continue
        suffix = Path(f.filename or "").suffix.lower() or ".png"
        if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
            suffix = ".png"
        dest = dest_dir / f"{cid}_{stamp}_{i}{suffix}"
        dest.write_bytes(data)
        refs.append(str(dest))
        added.append(str(dest))

    if not added:
        raise HTTPException(400, "所有文件都是空的")

    try:
        out = service.upsert_character(cid, cur.get("name", cid),
                                       cur.get("description", ""), refs, force=force)
    except service.LockedError as e:
        for p in added:
            Path(p).unlink(missing_ok=True)
        raise HTTPException(409, str(e)) from e

    if 0 <= primary_index < len(added):
        want = added[primary_index]
        reordered = [want] + [r for r in refs if r != want]
        out = service.upsert_character(cid, cur.get("name", cid),
                                       cur.get("description", ""), reordered,
                                       force=True)
    return {**out, "added": added}


@app.post("/api/characters/{cid}/lock")
def lock_character(cid: str, body: dict):
    try:
        return service.set_character_lock(cid, bool(body.get("locked", True)))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/characters/{cid}/verified")
def verify_character(cid: str, body: dict):
    try:
        return service.set_character_verified(cid, bool(body.get("verified", True)))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/characters/{cid}/override")
def set_cast_override(pid: str, cid: str, body: dict):
    """本剧专用角色覆盖（描述 / 参考图），不影响其他项目。"""
    try:
        return service.set_project_character_override(
            pid, cid,
            description=body.get("description"),
            refs=body.get("refs"))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/projects/{pid}/characters/{cid}/override")
def clear_cast_override(pid: str, cid: str):
    return service.clear_project_character_override(pid, cid)


@app.get("/api/characters/{cid}/versions")
def character_versions(cid: str):
    return {"versions": service.list_versions("character", cid)}


@app.get("/api/characters")
def characters():
    return {"characters": service.list_characters()}


@app.post("/api/characters")
def save_character(body: CharacterIn):
    return service.upsert_character(body.character_id, body.name, body.description, body.refs)


class ImportIn(BaseModel):
    project_id: str
    markdown: str
    group_id: str = ""
    group_name: str = ""


@app.post("/api/import/preview")
def import_preview(body: ImportIn):
    try:
        return service.preview_import(body.project_id, body.markdown)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/import")
def import_script(body: ImportIn):
    try:
        res = service.import_markdown(body.project_id, body.markdown)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # 分组：给了 group_id 就用它；只给了 group_name 就按名字新建
    gid = (body.group_id or "").strip()
    if not gid and getattr(body, "group_name", ""):
        try:
            gid = service.ensure_group(body.group_name)["id"]
        except ValueError:
            gid = ""
    if gid:
        service.set_project_group(body.project_id, gid)
    res["group_id"] = gid
    return res


@app.post("/api/projects/{pid}/preview")
def preview(pid: str, body: dict):
    shot_id = str(body.get("shot_id") or "")
    if not shot_id:
        raise HTTPException(400, "需要 shot_id")
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    job = service.enqueue_job(pid, "preview", {"shot_id": shot_id})
    return {"ok": True, "job": job, "executor": runner.enabled()}


@app.get("/assets/previews/{pid}/{sid}")
def preview_asset(pid: str, sid: str):
    p = db.DATA_DIR / "projects" / pid / "previews" / f"{sid}.jpg"
    if not p.is_file():
        raise HTTPException(404, "还没有预览")
    return FileResponse(p, media_type="image/jpeg")


@app.post("/api/projects/{pid}/shots/{sid}/compile-preview")
def compile_shot_preview(pid: str, sid: str, body: CompilePreviewIn):
    """只读编译未保存草稿；POST 仅用于提交草稿，不代表持久化或入队。"""
    try:
        return render_adapter.compile_preview(pid, sid, body.prompt)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/shots/{sid}/assign")
def assign_shot(pid: str, sid: str, body: dict):
    try:
        return service.assign_shot(
            pid, sid,
            cast_add=body.get("cast_add"),
            cast_remove=body.get("cast_remove"),
            scene_id=body.get("scene_id"),
            transition=body.get("transition"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/shots/auto-transition")
def auto_transition(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return service.auto_transitions(pid)


@app.post("/api/projects/{pid}/shots/batch")
def batch_update(pid: str, body: dict):
    shot_ids = body.get("shot_ids") or []
    edit = body.get("set") or {}
    try:
        return service.batch_update(pid, shot_ids,
                                    scene_id=edit.get("scene_id"),
                                    transition=edit.get("transition"),
                                    cast_add=edit.get("cast_add"),
                                    cast_remove=edit.get("cast_remove"),
                                    chain_ref=edit.get("chain_ref"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/shots/{sid}/first-frame")
async def upload_first_frame(pid: str, sid: str, file: UploadFile = File(...)):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    stamp = db.now().replace(":", "").replace("-", "")
    dest = db.DATA_DIR / "projects" / pid / "firstframes" / f"{sid}_{stamp}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    try:
        return service.set_first_frame(pid, sid, str(dest))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/projects/{pid}/shots/{sid}/first-frame")
def clear_first_frame(pid: str, sid: str):
    try:
        return service.clear_first_frame(pid, sid)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/shots/{sid}/first-frame/from-previous")
def first_frame_from_previous(pid: str, sid: str):
    """Pin this shot's first frame to the previous shot's last frame (one click)."""
    import subprocess
    shots = service.list_shots(pid)
    ids = [s["shot_id"] for s in shots]
    if sid not in ids:
        raise HTTPException(404, "镜头不存在")
    pos = ids.index(sid)
    if pos == 0:
        raise HTTPException(400, "第一镜没有上一镜")
    prev = service.latest_take(pid, ids[pos - 1])
    if not prev or not Path(prev["path"]).is_file():
        raise HTTPException(400, f"上一镜 {ids[pos - 1]} 还没有片段")
    src = Path(prev["path"])
    dest = db.DATA_DIR / "projects" / pid / "firstframes" / f"{sid}_{db.now().replace(':', '').replace('-', '')}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.15", "-i", str(src),
                    "-frames:v", "1", "-update", "1", "-q:v", "2", str(dest)],
                   check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not dest.is_file():
        raise HTTPException(500, "抽帧失败")
    return service.set_first_frame(pid, sid, str(dest))


@app.get("/assets/lastframe/{pid}/{sid}")
def last_frame_asset(pid: str, sid: str, version: int | None = None):
    """Last frame of a shot's take — what a chain successor will start from."""
    import subprocess
    take = (service.latest_take(pid, sid) if version is None
            else service.take_by_version(pid, sid, version))
    if not take or not Path(take["path"]).is_file():
        raise HTTPException(404, "这一镜还没有片段")
    src = Path(take["path"])
    cache = src.with_name(f"{src.stem}_last.jpg")
    if not cache.is_file() or cache.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.15", "-i", str(src),
                        "-frames:v", "1", "-update", "1", "-q:v", "2", str(cache)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not cache.is_file():
        raise HTTPException(500, "抽帧失败")
    return FileResponse(cache, media_type="image/jpeg")


@app.get("/assets/poster/{pid}/{sid}")
def poster_asset(pid: str, sid: str, version: int | None = None):
    """首帧缩略图：从片段视频抽第一帧并缓存，供合成列表展示。"""
    import subprocess
    take = (service.latest_take(pid, sid) if version is None
            else service.take_by_version(pid, sid, version))
    if not take or not Path(take["path"]).is_file():
        raise HTTPException(404, "这一镜还没有片段")
    src = Path(take["path"])
    cache = src.with_name(f"{src.stem}_poster.jpg")
    if not cache.is_file() or cache.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0", "-i", str(src),
                        "-frames:v", "1", "-update", "1", "-q:v", "2", str(cache)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not cache.is_file():
        raise HTTPException(500, "抽帧失败")
    return FileResponse(cache, media_type="image/jpeg")


@app.get("/assets/output-poster/{pid}/{name}")
def output_poster(pid: str, name: str):
    """成片首帧缩略图：从合成好的 mp4 抽第一帧并缓存，供资产管理页展示。"""
    import subprocess
    try:
        src = service.output_file(pid, name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    cache = src.with_name(f"{src.stem}_poster.jpg")
    if not cache.is_file() or cache.stat().st_mtime < src.stat().st_mtime:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "0", "-i", str(src),
                        "-frames:v", "1", "-update", "1", "-q:v", "2", str(cache)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not cache.is_file():
        raise HTTPException(500, "抽帧失败")
    return FileResponse(cache, media_type="image/jpeg")


@app.get("/assets/firstframes/{pid}/{sid}")
def first_frame_asset(pid: str, sid: str):
    with db.txn() as conn:
        row = conn.execute("SELECT first_frame FROM shots WHERE project_id=? AND shot_id=?",
                           (pid, sid)).fetchone()
    path = (row["first_frame"] if row else "") or ""
    if not path or not Path(path).is_file():
        raise HTTPException(404, "没有指定首帧")
    return FileResponse(path)


@app.post("/api/projects/{pid}/shots/{sid}/adopt")
def adopt_take(pid: str, sid: str):
    try:
        return service.adopt_take(pid, sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/assemble")
def assemble(pid: str, body: dict | None = None):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    body = body or {}
    try:
        return service.assemble(pid, int(body.get("width") or 1344),
                                int(body.get("height") or 768),
                                bool(body.get("with_subs", True)),
                                bool(body.get("allow_stale", False)),
                                body.get("shot_ids") or None)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e


@app.get("/outputs/{pid}/{name}")
def output_file(pid: str, name: str):
    try:
        p = service.output_file(pid, name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return FileResponse(p, media_type="video/mp4", filename=name)


@app.post("/api/outputs/{pid}/{name}/reveal")
def reveal_output(pid: str, name: str):
    """在本机资源管理器里打开成片所在目录并选中该文件（仅同机可见）。"""
    import subprocess
    try:
        src = service.output_file(pid, name)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    explorer = "/mnt/c/Windows/explorer.exe"
    if not Path(explorer).is_file():
        raise HTTPException(400, "当前环境不是 WSL/Windows，无法打开资源管理器")
    try:
        win = subprocess.run(["wslpath", "-w", str(src)], capture_output=True,
                             text=True, timeout=5).stdout.strip()
    except Exception:
        win = ""
    target = win or str(src)
    try:
        subprocess.Popen([explorer, f"/select,{target}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        raise HTTPException(500, f"打开资源管理器失败：{e}") from e
    return {"ok": True, "path": target, "dir": str(src.parent)}


@app.delete("/api/projects/{pid}/shots/{sid}/takes")
def delete_take(pid: str, sid: str, version: int | None = None):
    try:
        return service.delete_take(pid, sid, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/projects/{pid}/shots/{sid}/takes")
def list_takes(pid: str, sid: str):
    return {"takes": service.latest_takes(pid, sid)}


@app.get("/api/projects/{pid}/shots/{sid}/takes/all")
def takes_all(pid: str, sid: str):
    return {"takes": service.all_takes(pid, sid)}


@app.post("/api/projects/{pid}/shots/{sid}/takes/{version}/choose")
def choose_take(pid: str, sid: str, version: int):
    try:
        return service.choose_take(pid, sid, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/shots/{sid}/takes/{version}/adopt")
def adopt_take_version(pid: str, sid: str, version: int):
    try:
        return service.adopt_take(pid, sid, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/takes/choose-latest")
def choose_latest(pid: str):
    return service.choose_latest_all(pid)


@app.post("/api/projects/{pid}/shots/pass-all")
def pass_all_shots(pid: str):
    return service.pass_all(pid)


@app.post("/api/projects/{pid}/shots/{sid}/move")
def move_shot(pid: str, sid: str, body: dict):
    try:
        return service.move_shot(pid, sid, body.get("before"), bool(body.get("to_end")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------------------------------------------------------------- 音频床

BED_JOBS: dict[str, dict] = {}


@app.post("/api/projects/{pid}/audio/upload")
async def upload_bed(pid: str, kind: str, name: str, request: Request):
    """把自定义音频当整集床（原始二进制上传，省掉 multipart 依赖）。"""
    import subprocess
    from pathlib import Path as _P

    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    if kind not in ("bgm", "ambience"):
        raise HTTPException(400, "kind 只能是 bgm 或 ambience")
    body = await request.body()
    if not body:
        raise HTTPException(400, "空文件")
    dst = _P("/tmp") / f"upload_{pid}_{kind}{_P(name).suffix or '.m4a'}"
    dst.write_bytes(body)
    out = service.bed_path(pid, kind, "custom")
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(dst),
                         "-vn", "-c:a", "aac", "-b:a", "192k", str(out)],
                        capture_output=True, text=True)
    dst.unlink(missing_ok=True)
    if cp.returncode != 0 or not out.is_file():
        raise HTTPException(400, f"音频转码失败：{(cp.stderr or '')[-200:]}")
    service.set_project_audio(pid, {kind: {"mode": "file", "path": str(out)}})
    return {"ok": True, "kind": kind, "path": str(out), "variant": "custom"}


@app.get("/assets/audio/{pid}/{name}")
def audio_asset(pid: str, name: str):
    from fastapi.responses import FileResponse as _FR
    from pathlib import Path as _P

    f = service.bed_path(pid, name.replace(".m4a", ""))
    if not f.is_file():
        raise HTTPException(404, "没有这个音频")
    return _FR(f, media_type="audio/mp4", filename=_P(f).name)


@app.get("/api/projects/{pid}/audio/preview")
def audio_preview(pid: str, seconds: float = 12.0):
    """拿第一镜的片段 + 两条床混一小段，先听再决定。"""
    import subprocess
    import sys
    from pathlib import Path as _P

    shots = service.recompute_fingerprints(pid)
    take = None
    for sh in shots:
        t2 = service.chosen_take(pid, sh["shot_id"])
        if t2 and _P(t2["path"]).is_file():
            take = t2
            break
    if not take:
        raise HTTPException(400, "还没有任何片段可以试听")
    src = _P(take["path"])
    out = service.BEDS_DIR / f"{pid}_preview.mp4"
    tmp = service.BEDS_DIR / f"{pid}_preview_src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-t", str(seconds),
                    "-i", str(src), "-c:v", "copy", "-c:a", "aac", str(tmp)],
                   capture_output=True, text=True)
    args = ["ffmpeg", "-y", "-v", "error"]
    bgm, bgm_rep = service.resolve_bed(pid, "bgm")
    amb, amb_rep = service.resolve_bed(pid, "ambience")
    if not bgm and not amb:
        raise HTTPException(400, f"还没有可用的音频床（bgm: {bgm_rep.get('reason')}；"
                                 f"ambience: {amb_rep.get('reason')}）")
    # 直接复用 merge 的铺床逻辑：把 tmp 当"成片"再混一次
    sys.path.insert(0, str(service.REPO_DIR / "scripts"))
    import merge as merge_mod  # noqa: PLC0415

    merged = merge_mod.lay_beds(
        tmp,
        Path(bgm) if bgm else None,
        Path(amb) if amb else None,
        float((service.get_project(pid) or {}).get("audio", {}).get("bgm", {}).get("gain_db") or -18),
        float((service.get_project(pid) or {}).get("audio", {}).get("ambience", {}).get("gain_db") or -26),
        bool((service.get_project(pid) or {}).get("audio", {}).get("bgm", {}).get("duck_under_voice")),
        48000, 600)
    _P(merged).replace(out)
    tmp.unlink(missing_ok=True)
    return {"url": f"/assets/audio/{pid}/{out.name}", "seconds": seconds}


@app.post("/api/projects/{pid}/audio/generate")
def generate_bed(pid: str, kind: str = "bgm"):
    """后台用 H3 生成一条床（低分辨率短镜，只取音轨）。"""
    import subprocess
    import threading

    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    if kind not in ("bgm", "ambience"):
        raise HTTPException(400, "kind 只能是 bgm 或 ambience")
    if (BED_JOBS.get(pid) or {}).get("running"):
        raise HTTPException(409, "已经在生成了")

    out = service.bed_path(pid, kind)
    state = {"running": True, "kind": kind, "log": "", "path": "", "started": time.time()}
    BED_JOBS[pid] = state

    def work() -> None:
        try:
            cp = subprocess.run(
                [str(service.VENV_PY), "scripts/make_bed.py", "--kind", kind,
                 "--out", str(out), "--seconds", "10"],
                cwd=str(service.REPO_DIR), capture_output=True, text=True, timeout=3600)
            state["log"] = ((cp.stdout or "") + (cp.stderr or ""))[-1500:]
            if cp.returncode == 0 and out.is_file():
                state["path"] = str(out)
                service.set_project_audio(pid, {kind: {"mode": "auto", "path": str(out)}})
            else:
                state["error"] = "生成失败，看 log"
        except Exception as e:  # noqa: BLE001
            state["error"] = str(e)[:200]
        finally:
            state["running"] = False

    threading.Thread(target=work, daemon=True, name="bed-gen").start()
    return {"ok": True, "kind": kind, "status": "started"}


@app.delete("/api/projects/{pid}/audio/{kind}")
def delete_bed(pid: str, kind: str):
    """删掉这条床（文件 + 配置）：自定义和模型生成的都清掉。"""
    if kind not in ("bgm", "ambience"):
        raise HTTPException(400, "kind 只能是 bgm 或 ambience")
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    removed = service.delete_beds(pid, kind)
    service.set_project_audio(pid, {kind: {"mode": "off", "path": ""}})
    return {"ok": True, "kind": kind, **removed}


@app.post("/api/projects/{pid}/audio/use-path")
def use_audio_path(pid: str, body: dict):
    """把磁盘上任意一个音频文件设为整集床（bgm / ambience）。"""
    import subprocess
    from pathlib import Path as _P

    kind = str(body.get("kind") or "bgm")
    raw = str(body.get("path") or "")
    if kind not in ("bgm", "ambience"):
        raise HTTPException(400, "kind 只能是 bgm 或 ambience")
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    src = _P(raw).expanduser()
    if not src.is_file():
        raise HTTPException(400, f"文件不存在：{raw}")
    if src.suffix.lower() not in (".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg"):
        raise HTTPException(400, "只支持 m4a/mp3/wav/flac/aac/ogg")
    out = service.bed_path(pid, kind, "custom")
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn",
                         "-c:a", "aac", "-b:a", "192k", str(out)],
                        capture_output=True, text=True)
    if cp.returncode != 0 or not out.is_file():
        raise HTTPException(400, f"转码失败：{(cp.stderr or '')[-200:]}")
    service.set_project_audio(pid, {kind: {"mode": "file", "path": str(out)}})
    return {"ok": True, "kind": kind, "path": str(out), "source": str(src)}


@app.get("/api/system/audio-files")
def system_audio_files(path: str = ""):
    """浏览服务器目录里的音频文件（给"系统目录选择"用）。"""
    from pathlib import Path as _P

    base = _P(path).expanduser() if path else _P.home()
    if not base.is_dir():
        raise HTTPException(400, f"目录不存在：{path}")
    exts = (".m4a", ".mp3", ".wav", ".flac", ".aac", ".ogg")
    dirs, files = [], []
    for child in sorted(base.iterdir()):
        try:
            if child.is_dir():
                dirs.append({"name": child.name, "path": str(child)})
            elif child.suffix.lower() in exts:
                files.append({"name": child.name, "path": str(child),
                              "size": child.stat().st_size})
        except OSError:
            continue
    return {"path": str(base), "dirs": dirs[:200], "files": files[:200]}


@app.get("/api/projects/{pid}/audio/status")
def bed_status(pid: str):
    st = BED_JOBS.get(pid) or {}
    out = {k: st.get(k) for k in ("running", "kind", "log", "path", "error")}
    aud = (service.get_project(pid) or {}).get("audio") or {}
    out["exists"] = {}
    out["in_use"] = {}
    for k in ("bgm", "ambience"):
        path, rep = service.resolve_bed(pid, k)
        out["exists"][k] = bool(path)
        out["in_use"][k] = {"path": path, **rep}
    return out


@app.get("/api/projects/{pid}/stats")
def project_stats(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return service.stats(pid)


@app.get("/api/projects/{pid}/review-queue")
def review_queue(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    shots = service.recompute_fingerprints(pid)
    out = []
    for sh in shots:
        st = service.shot_state(pid, sh)
        if st["state"] == "review" and st["take"]:
            out.append({"shot_id": sh["shot_id"], "scene_id": sh["scene_id"],
                        "cast": sh["cast"], "camera": sh["camera"],
                        "dialogue": sh["dialogue"], "transition": sh["transition"],
                        "take": st["take"]})
    return {"shots": out, "total": len(out)}


@app.get("/api/projects/{pid}/export/markdown")
def export_markdown(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    from fastapi.responses import PlainTextResponse
    from ..export import markdown
    return PlainTextResponse(markdown.build_markdown(pid),
                             headers={"content-disposition":
                                      f'attachment; filename="{pid}.md"'})


@app.get("/api/projects/{pid}/export/edl")
def export_edl(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    from ..export import timeline
    data = timeline.build_edl(pid)
    return JSONResponse(data, headers={
        "content-disposition": f'attachment; filename="{pid}.edl"'})


@app.get("/api/projects/{pid}/export/otio")
def export_otio(pid: str):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    from ..export import timeline
    return JSONResponse(timeline.build_otio(pid), headers={
        "content-disposition": f'attachment; filename="{pid}.otio"'})


def _spa_html():
    """React SPA 入口；未构建时回落到旧版单文件。"""
    dist = WEB / "dist" / "index.html"
    return _html(dist) if dist.is_file() else _html(WEB / "index.html")


@app.get("/app")
def spa_index():
    """React 版界面（studio/web/dist/）。"""
    return _spa_html()


@app.get("/app/{path:path}")
def spa_fallback(path: str):
    """React Router 子路径 fallback：直接 /app/assets、/app/create/... 都返回 index.html。"""
    # 不拦截 /app/api/...、/app/assets/... 这类真实子资源
    if path and (path.startswith("api/") or path.startswith("assets/")
                 or path.startswith("static/") or path.startswith("media/")
                 or path.startswith("outputs/")):
        from fastapi import HTTPException
        raise HTTPException(404, f"not found: /app/{path}")
    return _spa_html()


@app.get("/review")
def review_page():
    return _html(WEB / "review.html")


@app.get("/api/projects/{pid}/validate")
def validate_script(pid: str):
    """剧本契约校验：UI 的就绪条和导入结果都用这个。"""
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    from studio.core import validate as _validate
    return _validate.validate_project(pid)


@app.post("/api/projects/{pid}/shots/{sid}/chain-ref")
def set_chain_ref(pid: str, sid: str, body: ChainRefIn):
    """开关「接上一镜末帧」：人物镜把它当场景参考图，空镜走真首帧。"""
    try:
        return {"shots": service.set_chain_ref(pid, sid, body.enabled)}
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/projects/{pid}/rename")
def rename_project(pid: str, body: RenameIn):
    """只改标题，不重建项目（镜头、版本、任务全部保留）。"""
    try:
        return service.rename_project(pid, body.title)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/plan")
def make_plan(pid: str, body: PlanIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return service.plan(pid, body.shot_ids, body.draft, force=body.force)


@app.get("/api/projects/{pid}/audio")
def get_audio(pid: str):
    project = service.get_project(pid)
    if not project:
        raise HTTPException(404, "项目不存在")
    return {"audio": project.get("audio") or {}}


@app.put("/api/projects/{pid}/audio")
def put_audio(pid: str, body: dict):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    try:
        return {"audio": service.set_project_audio(pid, body)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/confirm")
def confirm(pid: str, body: ConfirmIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    stored = service.PLAN_STORE.get(body.plan_id)
    if not stored:
        raise HTTPException(409, "计划不存在或已过期，请重新生成计划")
    if stored["project_id"] != pid:
        raise HTTPException(409, "计划属于其他项目，请重新生成")
    fresh = service.plan(pid, stored["shot_ids"], stored["draft"],
                        force=stored.get("force", False))
    if fresh["plan_id"] != body.plan_id:
        raise HTTPException(409, "计划已过期（剧本或资产改过了），请重新生成计划")
    if fresh["blocking"]:
        raise HTTPException(400, "计划含阻断问题：" + fresh["blocking"][0]["msg"])
    if not fresh["shots"]:
        raise HTTPException(400, "没有需要渲染的镜头")
    shot_ids = []
    skipped = []
    for s in fresh["shots"]:
        sid = s["shot_id"]
        if service.active_job_for_shot(pid, sid):
            skipped.append(sid)
            continue
        shot_ids.append(sid)
    if not shot_ids:
        raise HTTPException(409, "这些镜头已经在队列里了（"
                            + "、".join(skipped) + "），等它们渲完再排")
    # one job per shot: each is independently cancellable and shows its own
    # progress, while the executor still runs them strictly in order
    jobs = []
    for sid in shot_ids:
        jobs.append(service.enqueue_job(
            pid, "render",
            {"shots": [sid], "draft": body.draft, "plan_id": body.plan_id,
             "max_shots": 0, "prompt_mode": body.prompt_mode or "",
             "unet": getattr(body, "unet", "") or "",
             "retries_left": max(0, int(getattr(body, "retries", 0) or 0))}))
    return {"ok": True, "jobs": jobs, "job": jobs[0], "executor": runner.enabled(),
            "count": len(jobs), "skipped": skipped}


@app.post("/api/projects/{pid}/review/batch")
def review_batch(pid: str, body: dict):
    try:
        return service.batch_review(pid, [str(x) for x in (body.get("shot_ids") or [])],
                                    str(body.get("verdict") or "pass"),
                                    str(body.get("reason") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/review")
def review(pid: str, body: ReviewIn):
    try:
        return service.review_shot(pid, body.shot_id, body.verdict, body.reason)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    job = service.get_job(jid)
    if not job:
        raise HTTPException(404, "任务不存在")
    killed = False
    if job["state"] == "running":
        killed = runner.kill(jid)
    try:
        return {"ok": True, "killed": killed, "job": service.cancel_job(jid)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/projects/{pid}/clear-queue")
def clear_queue(pid: str):
    """Cancel everything belonging to this project, including a running shot."""
    active = service.cancel_project_jobs(pid)
    killed = []
    for jid in active["running"]:
        if runner.kill(jid):
            killed.append(jid)
        try:
            service.cancel_job(jid, "stopped by user")
        except ValueError:
            pass
    for jid in active["ids"]:
        try:
            service.cancel_job(jid, "cleared by user")
        except ValueError:
            pass
    return {"cancelled": active["cancelled"], "killed": killed, "ids": active["ids"]}


@app.post("/api/stop")
def stop_everything():
    """Global panic switch: kill the running render and cancel all queued work."""
    jobs = [j for j in service.list_jobs(limit=50)
            if j["state"] in ("queued", "running", "paused")]
    killed = []
    for j in jobs:
        if j["state"] == "running" and runner.kill(j["id"]):
            killed.append(j["id"])
    for j in jobs:
        try:
            service.cancel_job(j["id"], "stopped by user")
        except ValueError:
            pass
    return {"ok": True, "cancelled": len(jobs), "killed": killed}


@app.get("/api/jobs/active")
def active_jobs():
    """当前在跑 / 排队的任务，统一给状态栏用（视频、图片、音乐都认得出）。"""
    import json as _json
    out = []
    for job in service.list_jobs(limit=200):
        if job.get("state") not in ("running", "queued"):
            continue
        payload = _json.loads(job.get("payload") or "{}")
        kind = job.get("kind") or ""
        target = ""
        page = "/create/video/rendering"
        if kind == "render":
            target = "镜 " + "、".join(payload.get("shots") or [])
            page = "/create/video/rendering"
        elif kind == "music" or payload.get("kind") == "music":
            target = f"音乐 {float(payload.get('seconds') or 0):.0f}s"
            page = "/create/music"
        elif kind in ("asset", "preview"):
            target = f"{payload.get('kind') or '图片'}"
            page = "/create/text-to-image"
        elif kind in ("assemble", "merge", "export"):
            target = "合成"
            page = "/create/video/compose"
        prog = runner.progress_of(job["id"]) or {}
        out.append({
            "id": job["id"], "kind": kind, "state": job["state"],
            "project_id": job.get("project_id") or "",
            "target": target, "page": page,
            "created_at": job.get("created_at"), "started_at": job.get("started_at"),
            "pct": prog.get("pct"), "detail": prog.get("detail") or prog.get("stage") or "",
        })
    return {"jobs": out}


@app.get("/api/jobs/{jid}/progress")
def job_progress(jid: str):
    p = runner.progress_of(jid)
    job = service.get_job(jid)
    return {"job": job, "progress": p}


@app.get("/api/jobs")
def jobs(project_id: str | None = None):
    return {"jobs": service.list_jobs(project_id), "executor": runner.enabled()}


@app.get("/api/selftest")
def selftest():
    from . import selftest as st
    return st.run()


@app.get("/selftest")
def selftest_page():
    return _html(WEB / "selftest.html")


@app.get("/api/queue/global")
def queue_global():
    jobs = [j for j in service.list_jobs(limit=50) if j["state"] in ("running", "queued", "paused")]
    return {"active": jobs[:8], "count": len(jobs), "executor": runner.enabled()}


@app.get("/api/health")
def health():
    return {"ok": True, "time": db.now(), "executor": runner.enabled(),
            "data": str(db.DB_PATH)}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    try:
        return service.delete_project(pid)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/projects/{pid}/shots/{sid}")
def delete_shot(pid: str, sid: str):
    try:
        return service.delete_shot(pid, sid)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.delete("/api/characters/{cid}")
def delete_character(cid: str, force: bool = False):
    try:
        return service.delete_character(cid, force)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/characters/{cid}")
def character_detail(cid: str):
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise HTTPException(404, "角色不存在")
    return {"character_id": cid, "name": cur["name"], "locked": cur["locked"],
            "description": cur.get("description", ""), "refs": cur.get("refs", []),
            "primary": cur.get("primary_ref", "")}


@app.delete("/api/characters/{cid}/ref")
def delete_character_ref(cid: str, path: str):
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise HTTPException(404, "角色不存在")
    refs = [r for r in cur.get("refs", []) if r != path]
    if len(refs) == len(cur.get("refs", [])):
        raise HTTPException(404, "这张参考图不在角色里")
    p = Path(path)
    if p.is_file() and p.parent.name == "characters":
        p.unlink(missing_ok=True)
    return service.upsert_character(cid, cur["name"], cur.get("description", ""), refs)


@app.post("/api/characters/{cid}/refs/order")
def reorder_character_refs(cid: str, body: dict):
    """只改参考图顺序（第 1 张 = 主图）。不动描述、不动其他字段。

    前端拖动排序后调用；顺序必须是当前这几张的一个排列，防止误传丢图。
    """
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise HTTPException(404, "角色不存在")
    want = [str(x) for x in (body.get("refs") or [])]
    have = list(cur.get("refs") or [])
    if sorted(want) != sorted(have):
        raise HTTPException(400, "排序结果和现有图片不一致（不能借排序增删图）")
    return service.upsert_character(cid, cur["name"], cur.get("description") or "", want)


@app.post("/api/characters/{cid}/refs/{idx}/replace")
async def replace_character_ref(cid: str, idx: int, file: UploadFile = File(...)):
    """把第 idx 张换成新图，其余两张原样不动（“我只是想换这一张”）。"""
    data = await file.read()
    if not data:
        raise HTTPException(400, "空文件")
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise HTTPException(404, "角色不存在")
    refs = list(cur.get("refs") or [])
    if idx < 0 or idx >= len(refs):
        raise HTTPException(404, "没有这张参考图")

    suffix = Path(file.filename or "").suffix.lower() or ".png"
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        suffix = ".png"
    stamp = db.now().replace(":", "").replace("-", "")
    dest = db.DATA_DIR / "characters" / f"{cid}_{stamp}_r{idx}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)

    old = refs[idx]
    refs[idx] = str(dest)
    res = service.upsert_character(cid, cur["name"], cur.get("description") or "", refs)
    old_path = Path(old)
    if old_path.is_file() and old_path.parent.name == "characters" and old_path.name != dest.name:
        old_path.unlink(missing_ok=True)
    return res


@app.post("/api/characters/{cid}/ref/primary")
def set_character_primary(cid: str, body: dict):
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise HTTPException(404, "角色不存在")
    want = str(body.get("path") or "")
    refs = [r for r in cur.get("refs", []) if r != want]
    if want and want in cur.get("refs", []):
        refs.insert(0, want)
    else:
        raise HTTPException(400, "这张图不在角色里")
    return service.upsert_character(cid, cur["name"], cur.get("description", ""), refs)


@app.get("/assets/characters/{cid}/refs/{name}")
def character_ref_by_name(cid: str, name: str):
    """按文件名取参考图。

    为什么要这个：按序号（/ref/{i}）取图时，重排后同一个 URL 会指向另一张图，
    浏览器缓存会把旧图留在新地址上 —— 看起来就是"两张图变成一样了"。
    按文件名取，文件与 URL 一一对应，顺序怎么变都不会串。
    """
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid) or {}
    for p in cur.get("refs", []):
        if Path(p).name == name:
            fp = Path(p)
            if fp.is_file():
                return FileResponse(fp, headers={"Cache-Control": "public, max-age=86400"})
            break
    raise HTTPException(404, "没有这张参考图")


@app.get("/assets/characters/{cid}/ref/{idx}")
def character_ref_asset(cid: str, idx: int):
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid) or {}
    refs = cur.get("refs", [])
    if idx < 0 or idx >= len(refs):
        raise HTTPException(404, "没有这张参考图")
    p = Path(refs[idx])
    if not p.is_file():
        raise HTTPException(404, "文件丢失")
    return FileResponse(p)


@app.get("/assets/characters/{cid}")
def character_asset(cid: str):
    chars = {c["character_id"]: c for c in service.list_characters()}
    cur = chars.get(cid) or {}
    path = cur.get("primary_ref") or (cur.get("refs") or [""])[0]
    if not path or not Path(path).is_file():
        raise HTTPException(404, "没有参考图")
    return FileResponse(path)


@app.get("/assets/scenes/{pid}/{sid}")
def scene_asset(pid: str, sid: str):
    sc = service.get_scene(pid, sid) or {}
    path = sc.get("plate_path") or ""
    if not path or not Path(path).is_file():
        raise HTTPException(404, "没有母版图")
    return FileResponse(path)


@app.get("/media/{pid}/{shot_id}/{version}")
def media(pid: str, shot_id: str, version: int):
    p = service.take_path(pid, shot_id, version)
    if not p.is_file():
        raise HTTPException(404, "片段不存在")
    return FileResponse(p, media_type="video/mp4")


@app.get("/")
def index():
    """根路径跳 /app —— SPA 的 basename 是 /app，直接给 index.html 会白屏。"""
    from fastapi.responses import RedirectResponse as _RR

    return _RR("/app", status_code=302)


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


def _counts(shots: list[dict]) -> dict:
    c: dict[str, int] = {}
    for s in shots:
        c[s["state"]] = c.get(s["state"], 0) + 1
    c["total"] = len(shots)
    return c


# ---------------------------------------------------------------- 资产工坊候选图

class AssetAdoptIn(BaseModel):
    target: str              # "character" | "scene"
    character_id: str = ""
    project_id: str = ""
    scene_id: str = ""
    role: str = "main"       # scene: "main" 替换母版 / "alt" 加多角度


@app.get("/api/assets/candidates")
def list_asset_candidates():
    return {"candidates": assets.list_candidates()}


@app.get("/api/assets/catalog")
def assets_catalog(project_id: str = "", group_id: str = ""):
    """「角色和场景」页：按项目 / 分组筛选。"""
    return assets.catalog(project_id=project_id, group_id=group_id)


@app.get("/api/assets/groups")
def list_asset_groups():
    return {"groups": assets.list_groups()}


@app.post("/api/assets/groups")
def create_asset_group(body: dict):
    try:
        return assets.ensure_group(str(body.get("name") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class AssetCandidatePatch(BaseModel):
    project_id: str | None = None
    group_id: str | None = None
    kind: str | None = None


@app.patch("/api/assets/candidates/{cand_id}")
def patch_asset_candidate(cand_id: str, body: AssetCandidatePatch):
    try:
        c = assets.patch_candidate(
            cand_id, project_id=body.project_id,
            group_id=body.group_id, kind=body.kind)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not c:
        raise HTTPException(404, "候选图不存在")
    return c


@app.get("/api/assets/candidates/{cand_id}")
def get_asset_candidate(cand_id: str):
    c = assets.get_candidate(cand_id)
    if not c:
        raise HTTPException(404, "候选图不存在")
    return c


@app.get("/api/assets/candidates/{cand_id}/lyrics")
def candidate_lyrics(cand_id: str):
    """.lrc 歌词（时间轴是按行估算的，播放器用它做进度高亮）。"""
    from fastapi.responses import PlainTextResponse as _PT

    c = assets.get_candidate(cand_id)
    if not c:
        raise HTTPException(404, "候选不存在")
    text = assets.lyrics_of(c)
    if not text.strip():
        raise HTTPException(404, "这段音乐没有歌词")
    return _PT(text, media_type="text/plain; charset=utf-8")


@app.delete("/api/assets/candidates/{cand_id}")
def delete_asset_candidate(cand_id: str):
    ok = assets.delete_candidate(cand_id)
    if not ok:
        raise HTTPException(404, "候选图不存在")
    return {"ok": True}


@app.post("/api/assets/candidates/{cand_id}/adopt")
def adopt_asset_candidate(cand_id: str, body: AssetAdoptIn):
    """把候选图入库到角色图集或场景母版。"""
    if body.target not in ("character", "scene"):
        raise HTTPException(400, "target 必须是 character 或 scene")
    result = assets.adopt_candidate(
        cand_id,
        target_kind=body.target,
        character_id=body.character_id,
        project_id=body.project_id,
        scene_id=body.scene_id,
        role=body.role,
    )
    if not result.ok:
        raise HTTPException(400, result.error)
    return {"ok": True, "candidate": assets.get_candidate(cand_id), "adopted_to": result.adopted_to}


class AssetGenerateIn(BaseModel):
    kind: str = "character"   # character | scene | prop | emotion
    prompt: str
    negative: str = ""
    width: int = 2048
    height: int = 2048
    seed: int = -1       # -1 = random
    steps: int = 6
    count: int = 1       # 1–4 张
    project_id: str = ""
    group_id: str = ""
    refs: list[str] = []  # 角色 id（旧接口，保留兼容）
    ref_paths: list[str] = []  # 上传的参考图路径（不绑角色/项目），最多 4 张
    caption_zh: str = ""   # kind=music：中文风格说明（仅展示/留痕）
    lyrics: str = ""      # kind=music：歌词（留空=器乐）
    seconds: float = 60.0  # kind=music：时长（秒，≤360）


@app.get("/api/mv/presets")
def mv_presets():
    from ..core import mv as _mv

    return {"presets": _mv.list_presets()}


class MvPlanIn(BaseModel):
    music_id: str                       # 资产里的音乐候选 id
    preset_id: str                      # /api/mv/presets 里的 id
    seconds: float | None = None        # 不传就用音乐自己的时长


@app.post("/api/mv/plan")
def mv_plan(body: MvPlanIn):
    """把「一段音乐 + 一个地域/舞蹈预设」展开成一个可分镜渲染的项目。"""
    import uuid as _uuid
    from ..core import mv as _mv

    preset = _mv.get_preset(body.preset_id)
    if not preset:
        raise HTTPException(400, "没有这个 MV 预设")
    cand = assets.get_candidate(body.music_id)
    if not cand or cand.get("kind") != "music":
        raise HTTPException(404, "音乐不存在")
    params = cand.get("params") or {}
    seconds = float(body.seconds or params.get("seconds") or 0)
    if seconds <= 0:
        seconds = 40.0
    music = {"id": cand["id"], "name": Path(cand.get("path") or "").name}
    title, markdown = _mv.build_markdown(preset, music, seconds)

    pid = "p_mv_" + _uuid.uuid4().hex[:8]
    service.create_project(title=title, project_id=pid)
    service.import_markdown(pid, markdown)
    # 舞蹈类 MV 需要「舞者」这个角色：导入时若没建成，补一个空壳（之后可以给它挂参考图）
    known = {c["character_id"] for c in service.list_characters()}
    for sh in service.list_shots(pid):
        for cid in sh.get("cast") or []:
            if cid not in known:
                service.upsert_character(cid, "舞者",
                                         "年轻舞者，短黑发，深色练功服，动作干净", [])
                known.add(cid)
    # 音乐直接挂成该项目的 BGM
    service.set_project_audio(pid, {"bgm": {"mode": "file", "path": cand.get("path") or ""}})
    return {"ok": True, "project_id": pid, "title": title,
            "shots": len(service.list_shots(pid)), "seconds": seconds,
            "music": {"id": cand["id"], "duration": seconds}}


@app.post("/api/assets/refs/upload")
async def upload_ref(name: str, request: Request):
    """上传一张参考图（不绑角色、不绑项目），返回它在数据目录里的路径。"""
    import hashlib

    body = await request.body()
    if not body:
        raise HTTPException(400, "空文件")
    if len(body) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 20 MB")
    suffix = Path(name).suffix.lower() or ".png"
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(400, "只接受 png/jpg/webp")
    d = db.DATA_DIR / "refs"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / (hashlib.sha256(body).hexdigest()[:16] + suffix)
    dst.write_bytes(body)
    return {"path": str(dst), "name": name}


@app.post("/api/assets/generate")
def generate_asset(body: AssetGenerateIn):
    """把文生图任务排进同一条 GPU 队列（与渲染串行，不抢显存）。"""
    if body.kind not in assets.ASSET_KINDS:
        raise HTTPException(400, f"kind 必须是 {'/'.join(assets.ASSET_KINDS)}")
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(400, "提示词不能为空")
    if not (512 <= body.width <= 4096 and 512 <= body.height <= 4096):
        raise HTTPException(400, "宽高需在 512–4096 之间")
    # 模型要求 32 的倍数：预设里有 2731 这类值，就近取整而不是直接报错
    width = min(4096, max(512, round(body.width / 32) * 32))
    height = min(4096, max(512, round(body.height / 32) * 32))
    if not 1 <= body.steps <= 50:
        raise HTTPException(400, "步数需在 1–50 之间")
    if not 1 <= body.count <= 4:
        raise HTTPException(400, "张数需在 1–4 之间")
    if body.project_id and not service.get_project(body.project_id):
        raise HTTPException(400, "项目不存在")
    if body.group_id:
        with db.txn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM asset_groups WHERE id=?", (body.group_id,)).fetchone()
        if not exists:
            raise HTTPException(400, "素材分组不存在")

    ref_paths: list[str] = []
    if body.refs:
        if len(body.refs) > 4:
            raise HTTPException(400, "参考图最多 4 张")
        chars = {c["character_id"]: c for c in service.list_characters()}
        data_root = db.DATA_DIR.resolve()
        for cid in body.refs:
            c = chars.get(cid)
            if not c:
                raise HTTPException(400, f"角色不存在：{cid}")
            refs = c.get("refs") or []
            if not refs:
                raise HTTPException(400, f"角色「{c['name']}」还没有参考图")
            p = Path(refs[0]).resolve()
            try:
                p.relative_to(data_root)
            except ValueError as e:
                raise HTTPException(400, "参考图不在资产目录内") from e
            if not p.is_file():
                raise HTTPException(400, f"参考图文件不存在：{p.name}")
            ref_paths.append(str(p))

    if body.ref_paths:
        data_root = db.DATA_DIR.resolve()
        for raw in body.ref_paths:
            p = Path(raw).resolve()
            try:
                p.relative_to(data_root)
            except ValueError as e:
                raise HTTPException(400, "参考图不在资产目录内") from e
            if not p.is_file():
                raise HTTPException(400, f"参考图文件不存在：{p.name}")
            ref_paths.append(str(p))
    if len(ref_paths) > 4:
        raise HTTPException(400, "参考图最多 4 张")

    missing = [m for m in model_status.status() if m["state"] != "ready"]
    if missing:
        names = "、".join(m["file"] for m in missing)
        return JSONResponse(
            status_code=409,
            content={
                "error": "model_missing",
                "message": f"文生图模型未就绪（缺 {names}）。等下载完成后重启生成引擎，再回来生成。",
                "download": "/api/models/status",
            },
        )

    job = service.enqueue_job(body.project_id, "asset", {
        "kind": body.kind,
        "prompt": prompt,
        "negative": body.negative,
        "width": width,
        "height": height,
        "seed": body.seed,
        "steps": body.steps,
        "count": body.count,
        "project_id": body.project_id,
        "group_id": body.group_id,
        "refs": ref_paths,
    })
    active = len([j for j in service.list_jobs(limit=50)
                  if j["state"] in ("queued", "running")])
    return {
        "job_id": job["id"], "accepted": True, "count": body.count,
        "queue_position": active, "executor": runner.enabled(),
    }


# ---------------------------------------------------------------- 模型文件状态（只读）

@app.get("/api/health/deps")
def health_deps():
    """未登录也能看的健康摘要：只给状态和缺什么，不给磁盘/显存细节。"""
    try:
        rep = doctor.report()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "summary": f"体检失败：{str(e)[:80]}", "fail": [], "warn": []}
    return {
        "ok": rep["ok"],
        "summary": rep["summary"],
        "fail": [c["label"] for c in rep["checks"] if c["status"] == "fail"],
        "warn": [c["label"] for c in rep["checks"] if c["status"] == "warn"],
    }



@app.get("/api/skills")
def skills_view():
    """技能页：列出 .dsh/skills 下的 skill（含正文）。"""
    from ..core import skills_docs
    return skills_docs.payload()


@app.get("/api/spec")
def spec_docs_view():
    """规范页：60 条规则 + 历史教训，从 studio/data/spec/*.md 解析。"""
    from ..core import spec_docs
    return spec_docs.payload()


@app.get("/api/glossary")
def glossary(project_id: str = ""):
    """词库：全局 script-glossary.json；带 project_id 时叠加项目覆盖（暂未实现覆盖）。"""
    from ..core import glossary_store as gs
    try:
        data = gs.merged()
        path = gs.BASE
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"词库读取失败：{e}") from e
    cats = data.get("categories") or []
    return {
        "version": data.get("version", 1),
        "source": str(path),
        "project_id": project_id,
        "categories": [
            {"id": c.get("id"), "label": c.get("label"), "count": len(c.get("items") or []),
             "items": c.get("items") or []}
            for c in cats
        ],
        "rules": data.get("rules") or {},
        "total": sum(len(c.get("items") or []) for c in cats),
    }


class GlossaryItemIn(BaseModel):
    category: str
    item: str


@app.post("/api/glossary/items")
def glossary_add(body: GlossaryItemIn):
    """加一个词（写进 data_dir/glossary-overlay.json，不动仓库文件）。"""
    from ..core import glossary_store as gs
    if not body.category or not body.item.strip():
        raise HTTPException(400, "分类和词条都不能为空")
    try:
        gs.add_item(body.category, body.item)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    cats = gs.merged().get("categories") or []
    return {"ok": True, "categories": cats, "overlay": str(gs.overlay_path())}


@app.delete("/api/glossary/items")
def glossary_remove(category: str, item: str):
    """删/隐藏一个词。"""
    from ..core import glossary_store as gs
    gs.remove_item(category, item)
    cats = gs.merged().get("categories") or []
    return {"ok": True, "categories": cats, "overlay": str(gs.overlay_path())}


@app.post("/api/glossary/reset")
def glossary_reset(category: str = ""):
    """恢复默认：category 为空则清空全部覆盖。"""
    from ..core import glossary_store as gs
    gs.reset(category)
    cats = gs.merged().get("categories") or []
    return {"ok": True, "categories": cats, "overlay": str(gs.overlay_path())}


@app.get("/api/config")
def config_view():
    """只读展示：设置页用。不含密钥。"""
    from .. import config as _cfg
    return {
        "config_file": str(_cfg.config_file() or ""),
        "data_dir": str(_cfg.data_dir()),
        "content_dir": str(_cfg.content_dir()),
        "comfy": {"url": _cfg.get("comfy.url"), "root": _cfg.get("comfy.root")},
        "models": _cfg.load().get("models", {}),
        "render": _cfg.load().get("render", {}),
        "gpu": _cfg.load().get("gpu", {}),
    }



class AsBgmIn(BaseModel):
    project_id: str


@app.post("/api/assets/candidates/{cand_id}/as-bgm")
def candidate_as_bgm(cand_id: str, body: AsBgmIn):
    """把一段生成好的音乐设为该项目的 BGM 床（不下载、不重传）。"""
    import subprocess
    from pathlib import Path as _P

    cand = assets.get_candidate(cand_id)
    if not cand:
        raise HTTPException(404, "候选不存在")
    src = _P(cand.get("path") or "")
    if not src.is_file():
        raise HTTPException(400, "文件已不在磁盘上")
    if not service.get_project(body.project_id):
        raise HTTPException(404, "项目不存在")
    out = service.bed_path(body.project_id, "bgm")
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                         "-vn", "-c:a", "aac", "-b:a", "192k", str(out)],
                        capture_output=True, text=True)
    if cp.returncode != 0 or not out.is_file():
        raise HTTPException(400, f"转码失败：{(cp.stderr or '')[-200:]}")
    service.set_project_audio(body.project_id, {"bgm": {"mode": "file", "path": str(out)}})
    return {"ok": True, "path": str(out)}


@app.delete("/api/characters/{cid}")
def delete_character_api(cid: str):
    from . import assets as _assets  # noqa: F401  （保持导入习惯）
    from ..core import assets as _a

    if not _a.delete_character(cid):
        raise HTTPException(404, "角色不存在")
    return {"ok": True}


@app.get("/api/trash")
def list_trash_api():
    from ..core import trash as _trash

    items = _trash.list_trash()
    return {"items": items, "count": len(items)}


@app.post("/api/trash/empty")
def empty_trash_api():
    from ..core import trash as _trash

    return _trash.empty_trash()


@app.get("/api/doctor")
def doctor_report():
    """环境体检：能不能渲、缺什么。前端设置页可以直接展示。"""
    return doctor.report()


@app.get("/api/models/status")
def models_status():
    """扫描 ComfyUI/models 下两个文生图文件；只读，不做下载、不做校验。"""
    items = model_status.status()
    return {
        "models": items,
        "ready": all(m["state"] == "ready" for m in items),
        "downloading": any(m["state"] == "downloading" for m in items),
    }


# ---------------------------------------------------------------- 资产工坊静态目录

GENERATED_DIR = db.DATA_DIR / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets/generated", StaticFiles(directory=str(GENERATED_DIR)), name="generated")
