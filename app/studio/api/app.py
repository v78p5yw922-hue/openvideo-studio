"""FastAPI surface for the studio.

Rules baked in:
* No GPU work starts without `POST /confirm` and a plan whose id still matches.
* The executor is off unless STUDIO_ALLOW_RENDER=1 (set deliberately by a human).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..core import db, service
from ..exec import runner

WEB = Path(__file__).resolve().parent.parent / "web"

from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # start the executor only when a real server runs (uvicorn triggers lifespan);
    # tests import the app without starting background threads
    db.init_db()
    orphans = service.fail_orphan_running()
    if orphans:
        print(f"[startup] 清理了 {orphans} 个上次遗留的 running 任务")
    runner.start()
    yield


app = FastAPI(title="OpenVideo Studio", version="0.1.0", lifespan=lifespan)
db.init_db()


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


class ConfirmIn(BaseModel):
    plan_id: str
    draft: bool = False
    pause_after_one: bool = False


class ReviewIn(BaseModel):
    shot_id: str
    verdict: str
    reason: str = ""


# ------------------------------------------------------------------- routes

@app.get("/api/projects")
def projects():
    return {"projects": service.list_projects()}


@app.post("/api/projects")
def create_project(body: ProjectIn):
    if service.get_project(body.id):
        raise HTTPException(409, "项目已存在")
    return service.create_project(body.id, body.title, body.style)


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
                  for c in service.list_characters()]
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


@app.post("/api/characters/{cid}/lock")
def lock_character(cid: str, body: dict):
    try:
        return service.set_character_lock(cid, bool(body.get("locked", True)))
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


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


@app.post("/api/import/preview")
def import_preview(body: ImportIn):
    try:
        return service.preview_import(body.project_id, body.markdown)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/import")
def import_script(body: ImportIn):
    try:
        return service.import_markdown(body.project_id, body.markdown)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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
                                    cast_remove=edit.get("cast_remove"))
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
                                bool(body.get("with_subs", True)))
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


@app.delete("/api/projects/{pid}/shots/{sid}/takes")
def delete_take(pid: str, sid: str, version: int | None = None):
    try:
        return service.delete_take(pid, sid, version)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/projects/{pid}/shots/{sid}/takes")
def list_takes(pid: str, sid: str):
    return {"takes": service.latest_takes(pid, sid)}


@app.post("/api/projects/{pid}/shots/{sid}/move")
def move_shot(pid: str, sid: str, body: dict):
    try:
        return service.move_shot(pid, sid, body.get("before"), bool(body.get("to_end")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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


@app.get("/review")
def review_page():
    return FileResponse(WEB / "review.html")


@app.post("/api/projects/{pid}/plan")
def make_plan(pid: str, body: PlanIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    return service.plan(pid, body.shot_ids, body.draft)


@app.post("/api/projects/{pid}/confirm")
def confirm(pid: str, body: ConfirmIn):
    if not service.get_project(pid):
        raise HTTPException(404, "项目不存在")
    stored = service.PLAN_STORE.get(body.plan_id)
    if not stored:
        raise HTTPException(409, "计划不存在或已过期，请重新生成计划")
    if stored["project_id"] != pid:
        raise HTTPException(409, "计划属于其他项目，请重新生成")
    fresh = service.plan(pid, stored["shot_ids"], stored["draft"])
    if fresh["plan_id"] != body.plan_id:
        raise HTTPException(409, "计划已过期（剧本或资产改过了），请重新生成计划")
    if fresh["blocking"]:
        raise HTTPException(400, "计划含阻断问题：" + fresh["blocking"][0]["msg"])
    if not fresh["shots"]:
        raise HTTPException(400, "没有需要渲染的镜头")
    shot_ids = [s["shot_id"] for s in fresh["shots"]]
    # one job per shot: each is independently cancellable and shows its own
    # progress, while the executor still runs them strictly in order
    jobs = []
    for sid in shot_ids:
        jobs.append(service.enqueue_job(
            pid, "render",
            {"shots": [sid], "draft": body.draft, "plan_id": body.plan_id,
             "max_shots": 0}))
    return {"ok": True, "jobs": jobs, "job": jobs[0], "executor": runner.enabled(),
            "count": len(jobs)}


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
    return FileResponse(WEB / "selftest.html")


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
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


def _counts(shots: list[dict]) -> dict:
    c: dict[str, int] = {}
    for s in shots:
        c[s["state"]] = c.get(s["state"], 0) + 1
    c["total"] = len(shots)
    return c
