"""Domain service: projects, shots, takes, reviews, plans.

Every write goes through here so fingerprints and cascades cannot be forgotten
at a call site.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

from . import db
from . import refs as refs_mod
from .model import (RENDER_PARAMS_DEFAULT, cascade, next_version, sha256_file,
                    shot_fingerprint, take_name)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

# render budget measured on this box: 5 s @768x1344, 20 steps
MIN_PER_SHOT_FULL = 8.5
MIN_PER_SHOT_DRAFT = 5.0
DIALOGUE_MAX_CHARS = 24      # 5 s of Mandarin at a natural pace

EXEC_ALLOWED = False          # flipped by the confirmed-run endpoint only


def _refresh_all(project_id: str = "") -> None:
    """参考图/场景图变了就重算指纹与槽位计划。

    这些资产是全局或跨镜头共享的：换一张图，所有用到它的镜头都要失效，
    否则会拿旧图渲新镜、静默出片。
    """
    if project_id:
        recompute_fingerprints(project_id)
        return
    for pr in list_projects(include_archived=True):
        recompute_fingerprints(pr["id"])


# ---------------------------------------------------------------- projects

def project_settings(project_id: str) -> dict:
    """项目级设置：风格串 / 分辨率 / 时长上限 / 词库覆盖开关。"""
    with db.txn() as conn:
        row = conn.execute("SELECT settings_json, style, title FROM projects WHERE id=?",
                           (project_id,)).fetchone()
    if not row:
        raise KeyError(project_id)
    rec = db.row_to_dict(row)
    data = db.loads(rec.get("settings_json") or "{}", {})
    data.setdefault("style", rec.get("style") or "")
    data.setdefault("width", 1344)
    data.setdefault("height", 768)
    data.setdefault("max_duration", 10)
    data.setdefault("glossary_extra", [])
    return data


def update_project_settings(project_id: str, patch: dict) -> dict:
    cur = project_settings(project_id)
    allowed = {"style", "width", "height", "max_duration", "glossary_extra"}
    for k, v in (patch or {}).items():
        if k in allowed:
            cur[k] = v
    style = str(cur.pop("style", "") or "")
    with db.txn() as conn:
        conn.execute("UPDATE projects SET settings_json=?, style=?, updated_at=? WHERE id=?",
                     (json.dumps(cur, ensure_ascii=False), style, db.now(), project_id))
        db.emit(conn, project_id, "", "settings.saved", {})
    cur["style"] = style
    return cur


def rename_project(project_id: str, title: str) -> dict:
    """改项目标题（project id 不变，URL/资产/任务都不受影响）。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("标题不能为空")
    if not get_project(project_id):
        raise ValueError("项目不存在")
    with db.txn() as conn:
        conn.execute("UPDATE projects SET title=?, updated_at=? WHERE id=?",
                     (title, db.now(), project_id))
        db.emit(conn, project_id, "", "project.renamed", {"title": title})
    return get_project(project_id) or {}


def create_project(project_id: str, title: str, style: str = "") -> dict:
    now = db.now()
    with db.txn() as conn:
        conn.execute(
            "INSERT INTO projects(id,title,style,created_at,updated_at) VALUES (?,?,?,?,?)",
            (project_id, title, style, now, now))
        db.emit(conn, project_id, "", "project.created", {"title": title})
    return get_project(project_id)


DEFAULT_AUDIO = {"bgm": {"mode": "off", "path": "", "gain_db": -18.0, "duck_under_voice": True},
                 "ambience": {"mode": "off", "path": "", "gain_db": -26.0}}


def get_project(project_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    d = db.row_to_dict(row)
    if d:
        cfg = db.loads(d.pop("audio_json", "{}") or "{}", {})
        merged = {**DEFAULT_AUDIO, **(cfg or {})}
        for key in ("bgm", "ambience"):
            merged[key] = {**DEFAULT_AUDIO[key], **(merged.get(key) or {})}
        d["audio"] = merged
    return d


def set_project_audio(project_id: str, cfg: dict) -> dict:
    """项目级音频策略：逐镜不生成 BGM，后期铺整集 BGM + 全局 ambience。

    mode: off（不铺）/ file（用指定音频文件）/ auto（先留空，等生成后填 path）
    """
    with db.txn() as conn:
        row = conn.execute("SELECT audio_json FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError("项目不存在")
        cur = db.loads(row["audio_json"] or "{}", {})
        merged = {**DEFAULT_AUDIO, **(cur or {})}
        for key in ("bgm", "ambience"):
            if key in (cfg or {}):
                merged[key] = {**DEFAULT_AUDIO[key], **(merged.get(key) or {}), **(cfg[key] or {})}
        if "voice_duck" in (cfg or {}):
            merged["voice_duck"] = bool(cfg["voice_duck"])
        conn.execute("UPDATE projects SET audio_json=?, updated_at=? WHERE id=?",
                     (json.dumps(merged, ensure_ascii=False), db.now(), project_id))
        db.emit(conn, project_id, "", "project.audio", merged)
    return merged


def list_projects(include_archived: bool = False) -> list[dict]:
    sql = "SELECT * FROM projects"
    if not include_archived:
        sql += " WHERE archived=0"
    sql += " ORDER BY sort_order DESC, updated_at DESC"
    with db.txn() as conn:
        rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def reorder_projects(ids: list[str]) -> dict:
    """按传入顺序保存项目排序（数组第一项排最前）。"""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {"ok": True, "count": 0}
    with db.txn() as conn:
        conn.executemany(
            "UPDATE projects SET sort_order=? WHERE id=?",
            [(len(ids) - idx, pid) for idx, pid in enumerate(ids)],
        )
    return {"ok": True, "count": len(ids)}


# ------------------------------------------------------------------ groups
def list_groups() -> list[dict]:
    """项目分组（系列/剧集）。count = 该组下未归档的项目数。"""
    with db.txn() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.created_at, "
            "  (SELECT COUNT(*) FROM projects p WHERE p.group_id=g.id AND p.archived=0) AS count "
            "FROM project_groups g ORDER BY g.created_at").fetchall()
        ungrouped = conn.execute(
            "SELECT COUNT(*) AS n FROM projects WHERE (group_id='' OR group_id IS NULL) "
            "AND archived=0").fetchone()
    out = [dict(r) for r in rows]
    out.append({"id": "", "name": "未分组", "created_at": "", "count": ungrouped["n"] if ungrouped else 0})
    return out


def ensure_group(name: str) -> dict:
    """按名字找分组，没有就建（新建项目时不选分组 = 新建一个）。"""
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名不能为空")
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM project_groups WHERE name=?", (name,)).fetchone()
        if row:
            return dict(row)
        gid = "g_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
        conn.execute("INSERT OR IGNORE INTO project_groups(id,name,created_at) VALUES (?,?,?)",
                     (gid, name, db.now()))
        row = conn.execute("SELECT * FROM project_groups WHERE id=?", (gid,)).fetchone()
    return dict(row)


def rename_group(gid: str, name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名不能为空")
    with db.txn() as conn:
        conn.execute("UPDATE project_groups SET name=? WHERE id=?", (name, gid))
        row = conn.execute("SELECT * FROM project_groups WHERE id=?", (gid,)).fetchone()
    if not row:
        raise ValueError("分组不存在")
    return dict(row)


def delete_group(gid: str) -> dict:
    """删分组：组里的项目变成"未分组"，项目本身不删。"""
    with db.txn() as conn:
        conn.execute("UPDATE projects SET group_id='' WHERE group_id=?", (gid,))
        conn.execute("DELETE FROM project_groups WHERE id=?", (gid,))
    return {"ok": True}


def batch_projects(ids: list[str], action: str, group_id: str = "") -> dict:
    """批量：action = group / archive / unarchive。

    一次事务处理完，避免前端循环发 N 个请求（也避免中途失败一半）。
    """
    ids = [i for i in (ids or []) if i]
    if not ids:
        return {"ok": True, "count": 0}
    now = db.now()
    with db.txn() as conn:
        if action == "group":
            conn.executemany("UPDATE projects SET group_id=?, updated_at=? WHERE id=?",
                             [(group_id or "", now, i) for i in ids])
        elif action in ("archive", "unarchive"):
            flag = 1 if action == "archive" else 0
            conn.executemany("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                             [(flag, now, i) for i in ids])
        else:
            raise ValueError(f"未知批量操作：{action}")
        # 审计：批量操作影响面大，必须留痕（单项目的 archived 事件在 set_project_archived）
        for i in ids:
            db.emit(conn, i, "", f"project.batch_{action}",
                    {"group_id": group_id or "", "count": len(ids)})
    return {"ok": True, "count": len(ids), "action": action, "group_id": group_id}


def set_project_group(project_id: str, group_id: str) -> dict:
    with db.txn() as conn:
        conn.execute("UPDATE projects SET group_id=?, updated_at=? WHERE id=?",
                     (group_id or "", db.now(), project_id))
        db.emit(conn, project_id, "", "project.grouped", {"group_id": group_id or ""})
    return get_project(project_id) or {}


def set_project_archived(project_id: str, archived: bool) -> dict:
    with db.txn() as conn:
        conn.execute("UPDATE projects SET archived=?, updated_at=? WHERE id=?",
                     (1 if archived else 0, db.now(), project_id))
        db.emit(conn, project_id, "", "project.archived", {"archived": bool(archived)})
    return get_project(project_id) or {}


# ------------------------------------------------------------------ assets

def upsert_character(cid: str, name: str, description: str, refs: list[str],
                     force: bool = False, species: str = "") -> dict:
    prev = {c["character_id"]: c for c in list_characters()}.get(cid, {})
    primary = refs[0] if refs else ""
    new_sha = sha256_file(primary) if primary else ""
    old_sha = sha256_file(prev.get("primary_ref", "")) if prev.get("primary_ref") else ""
    if prev.get("locked") and new_sha and old_sha and new_sha != old_sha and not force:
        raise LockedError("角色主图已锁定；先解锁或确认替换")
    with db.txn() as conn:
        if new_sha and old_sha and new_sha != old_sha:
            _archive(conn, "character", cid, "", prev["primary_ref"], "replaced")
        conn.execute(
            """INSERT INTO characters(character_id,name,description,refs_json,primary_ref,
                                      species,updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(character_id) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 refs_json=excluded.refs_json, primary_ref=excluded.primary_ref,
                 species=CASE WHEN excluded.species='' THEN characters.species
                              ELSE excluded.species END,
                 updated_at=excluded.updated_at""",
            (cid, name, description, json.dumps(refs, ensure_ascii=False), primary,
             species or "", db.now()))
    # 参考图/描述变了：所有用到这个角色的项目都要重算指纹与槽位计划
    _refresh_all()
    return {"character_id": cid, "name": name, "refs": refs,
            "primary_ref": primary, "locked": bool(prev.get("locked"))}


class LockedError(RuntimeError):
    """Raised when an asset is frozen and a caller tries to replace it."""


def _archive(conn, kind: str, owner_id: str, project_id: str, path: str, note: str = "") -> int:
    row = conn.execute(
        "SELECT MAX(version) AS v FROM asset_versions WHERE kind=? AND owner_id=?",
        (kind, owner_id)).fetchone()
    v = (row["v"] or 0) + 1
    sha = sha256_file(path) if path else ""
    conn.execute(
        """INSERT INTO asset_versions(kind,owner_id,project_id,version,path,sha,created_at,note)
           VALUES (?,?,?,?,?,?,?,?)""",
        (kind, owner_id, project_id, v, path, sha, db.now(), note))
    return v


def list_versions(kind: str, owner_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute(
            """SELECT * FROM asset_versions WHERE kind=? AND owner_id=?
               ORDER BY version DESC""", (kind, owner_id)).fetchall()
    return [dict(r) for r in rows]


def set_scene_lock(project_id: str, scene_id: str, locked: bool) -> dict:
    with db.txn() as conn:
        cur = conn.execute("SELECT locked FROM scenes WHERE project_id=? AND scene_id=?",
                           (project_id, scene_id)).fetchone()
        if cur is None:
            raise ValueError("场景不存在")
        conn.execute("UPDATE scenes SET locked=? WHERE project_id=? AND scene_id=?",
                     (1 if locked else 0, project_id, scene_id))
        db.emit(conn, project_id, "", "scene.locked" if locked else "scene.unlocked",
                {"scene_id": scene_id})
    return get_scene(project_id, scene_id)


def set_character_lock(cid: str, locked: bool) -> dict:
    with db.txn() as conn:
        cur = conn.execute("SELECT character_id FROM characters WHERE character_id=?",
                           (cid,)).fetchone()
        if cur is None:
            raise ValueError("角色不存在")
        conn.execute("UPDATE characters SET locked=? WHERE character_id=?",
                     (1 if locked else 0, cid))
        db.emit(conn, "", "", "character.locked" if locked else "character.unlocked",
                {"character_id": cid})
    return {"character_id": cid, "locked": bool(locked)}


def upsert_scene(project_id: str, scene_id: str, *, name: str = "", camera: str = "",
                 props: str = "", light: str = "", plate_path: str = "",
                 force: bool = False) -> dict:
    prev = get_scene(project_id, scene_id) or {}
    plate_sha = sha256_file(plate_path) if plate_path else ""
    if (prev.get("locked") and plate_path and prev.get("plate_sha")
            and plate_sha != prev.get("plate_sha") and not force):
        raise LockedError("场景母版已锁定；先解锁或确认替换")
    with db.txn() as conn:
        if plate_path and prev.get("plate_path") and plate_sha != prev.get("plate_sha"):
            _archive(conn, "scene", scene_id, project_id, prev["plate_path"], "replaced")
        conn.execute(
            """INSERT INTO scenes(project_id,scene_id,name,camera,props,light,plate_path,plate_sha,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(project_id,scene_id) DO UPDATE SET
                 name=excluded.name, camera=excluded.camera, props=excluded.props,
                 light=excluded.light, plate_path=excluded.plate_path,
                 plate_sha=excluded.plate_sha, updated_at=excluded.updated_at""",
            (project_id, scene_id, name, camera, props, light, plate_path, plate_sha, db.now()))
    _refresh_all(project_id)
    return get_scene(project_id, scene_id)


def add_scene_plate(project_id: str, scene_id: str, path: str) -> dict:
    """追加一张场景参考图（不同机位/角度）。

    H3CastToVideo 的 scene_images 是一个批次，每帧占一个 <Picture> 槽位；
    同一场景给 2–3 个角度，模型才不会把空间压成一张平面图。
    """
    scene = get_scene(project_id, scene_id)
    if not scene:
        raise ValueError("场景不存在")
    plates = list(scene.get("extra_plates") or [])
    if path not in plates:
        plates.append(path)
    plates = plates[:4]                      # 9 个参考槽要留给角色，别贪多
    with db.txn() as conn:
        conn.execute("UPDATE scenes SET extra_plates_json=?, updated_at=? "
                     "WHERE project_id=? AND scene_id=?",
                     (json.dumps(plates, ensure_ascii=False), db.now(), project_id, scene_id))
        db.emit(conn, project_id, scene_id, "scene.plates", {"count": len(plates)})
    _refresh_all(project_id)
    return get_scene(project_id, scene_id) or {}


def remove_scene_plate(project_id: str, scene_id: str, index: int) -> dict:
    scene = get_scene(project_id, scene_id)
    if not scene:
        raise ValueError("场景不存在")
    plates = list(scene.get("extra_plates") or [])
    if 0 <= index < len(plates):
        plates.pop(index)
    with db.txn() as conn:
        conn.execute("UPDATE scenes SET extra_plates_json=?, updated_at=? "
                     "WHERE project_id=? AND scene_id=?",
                     (json.dumps(plates, ensure_ascii=False), db.now(), project_id, scene_id))
    return get_scene(project_id, scene_id) or {}


def restore_scene_plate(project_id: str, scene_id: str, version: int) -> dict:
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM asset_versions WHERE kind='scene' AND owner_id=? AND version=?""",
            (scene_id, version)).fetchone()
    if not row or not Path(row["path"]).is_file():
        raise ValueError("该版本不存在或文件已丢失")
    return upsert_scene(project_id, scene_id, name=(get_scene(project_id, scene_id) or {}).get("name", ""),
                        camera=(get_scene(project_id, scene_id) or {}).get("camera", ""),
                        props=(get_scene(project_id, scene_id) or {}).get("props", ""),
                        light=(get_scene(project_id, scene_id) or {}).get("light", ""),
                        plate_path=row["path"], force=True)


def _scene_row_to_dict(row) -> dict | None:
    d = db.row_to_dict(row)
    if d is None:
        return None
    d["extra_plates"] = db.loads(d.pop("extra_plates_json", "[]"), [])
    return d


def get_scene(project_id: str, scene_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM scenes WHERE project_id=? AND scene_id=?",
                           (project_id, scene_id)).fetchone()
    return _scene_row_to_dict(row)


def list_scenes(project_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY scene_id",
                            (project_id,)).fetchall()
    return [_scene_row_to_dict(r) for r in rows]


def list_characters(project_id: str | None = None) -> list[dict]:
    """角色库。

    传 project_id 时套用该项目在 project_cast 里的覆盖（描述 / 参考图），
    这样同一个角色可以在不同剧里有不同造型，互不影响。
    """
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM characters WHERE COALESCE(deleted,0)=0 ORDER BY name").fetchall()
        overrides: dict[str, dict] = {}
        if project_id:
            for r in conn.execute("SELECT * FROM project_cast WHERE project_id=?",
                                  (project_id,)).fetchall():
                overrides[r["character_id"]] = dict(r)
    out = []
    for r in rows:
        d = dict(r)
        d["refs"] = db.loads(d.pop("refs_json"), [])
        d["refs_verified"] = bool(d.get("refs_verified"))
        ov = overrides.get(d["character_id"])
        if ov:
            if ov.get("description_override"):
                d["description"] = ov["description_override"]
            ov_refs = db.loads(ov.get("refs_override_json") or "[]", [])
            if ov_refs:
                d["refs"] = ov_refs
                d["primary_ref"] = ov_refs[0]
            d["overridden"] = True
        else:
            d["overridden"] = False
        out.append(d)
    return out


def set_character_verified(cid: str, verified: bool) -> dict:
    """标记「参考图已核对」：勾了之后不再报 DESC_CONFLICT。"""
    with db.txn() as conn:
        cur = conn.execute("UPDATE characters SET refs_verified=?, updated_at=? WHERE character_id=?",
                           (1 if verified else 0, db.now(), cid))
        if cur.rowcount == 0:
            raise ValueError("角色不存在")
        db.emit(conn, "", cid, "character.verified", {"verified": bool(verified)})
    return {"character_id": cid, "refs_verified": bool(verified)}


def set_project_character_override(project_id: str, cid: str, *,
                                   description: str | None = None,
                                   refs: list[str] | None = None) -> dict:
    """本剧专用覆盖：只影响这个项目，不改角色库。"""
    if not get_project(project_id):
        raise ValueError("项目不存在")
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM project_cast WHERE project_id=? AND character_id=?",
                           (project_id, cid)).fetchone()
        cur_desc = row["description_override"] if row else ""
        cur_refs = db.loads(row["refs_override_json"], []) if row else []
        new_desc = cur_desc if description is None else description
        new_refs = cur_refs if refs is None else refs
        conn.execute(
            """INSERT INTO project_cast(project_id,character_id,description_override,
                                        refs_override_json,updated_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(project_id,character_id) DO UPDATE SET
                 description_override=excluded.description_override,
                 refs_override_json=excluded.refs_override_json,
                 updated_at=excluded.updated_at""",
            (project_id, cid, new_desc, json.dumps(new_refs, ensure_ascii=False), db.now()))
        db.emit(conn, project_id, cid, "project_cast.override",
                {"description": bool(new_desc), "refs": len(new_refs)})
    recompute_fingerprints(project_id)
    return {"project_id": project_id, "character_id": cid,
            "description_override": new_desc, "refs_override": new_refs}


def clear_project_character_override(project_id: str, cid: str) -> dict:
    with db.txn() as conn:
        conn.execute("DELETE FROM project_cast WHERE project_id=? AND character_id=?",
                     (project_id, cid))
    recompute_fingerprints(project_id)
    return {"project_id": project_id, "character_id": cid, "cleared": True}


def cast_refs(cast: list[str]) -> list[str]:
    if not cast:
        return []
    with db.txn() as conn:
        rows = conn.execute(
            f"SELECT refs_json FROM characters WHERE character_id IN ({','.join('?' * len(cast))})",
            cast).fetchall()
    refs: list[str] = []
    for r in rows:
        refs.extend(db.loads(r["refs_json"], []))
    return sorted(refs)


# ------------------------------------------------------------------- shots

SHOT_FIELDS = ("shot_id", "scene_id", "cast", "camera", "prompt", "dialogue",
               "sound", "music", "transition", "anchor", "duration", "first_frame")


def _shot_row_to_dict(row) -> dict:
    d = dict(row)
    d["cast"] = db.loads(d.pop("cast_json"), [])
    d["ref_plan"] = db.loads(d.pop("ref_plan_json", "{}") or "{}", {})
    return d


def list_shots(project_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM shots WHERE project_id=? ORDER BY ordinal",
                            (project_id,)).fetchall()
    return [_shot_row_to_dict(r) for r in rows]


def _prompt_slots(prompt: str) -> dict:
    """把 prompt 里的【机位】【人物】解析出来（失败就返回空）。"""
    try:
        import sys
        if str(SCRIPTS_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPTS_DIR))
        import prompt_compiler as pc  # noqa: PLC0415
        slots = pc.parse_slots(prompt or "")
        cam = pc.sg.parse_camera(slots.get("机位") or "") or {}
        people = pc.sg.parse_people(slots.get("人物") or "") or []
        return {"camera": cam, "people": people}
    except Exception:  # noqa: BLE001
        return {"camera": {}, "people": []}


def ref_plan_for(project_id: str, shot: dict) -> dict:
    """给一镜算槽位计划（人物图 + 场景图共享 9 个槽）。

    这是"用哪几张图"的唯一真相：渲染、校验、界面都读它。
    """
    cast_ids = list(shot.get("cast") or [])
    chars: dict[str, dict] = {}
    if cast_ids:
        with db.txn() as conn:
            rows = conn.execute(
                "SELECT character_id,name,refs_json FROM characters "
                f"WHERE character_id IN ({','.join('?' * len(cast_ids))})",
                cast_ids).fetchall()
        chars = {r["character_id"]: dict(r) for r in rows}

    overrides: dict[str, list[str]] = {}
    with db.txn() as conn:
        try:
            for r in conn.execute(
                    "SELECT character_id, refs_json FROM project_cast "
                    "WHERE project_id=? AND refs_json NOT IN ('', '[]')",
                    (project_id,)).fetchall():
                overrides[r["character_id"]] = db.loads(r["refs_json"], [])
        except Exception:  # noqa: BLE001  老库没有这张表
            pass

    parsed = _prompt_slots(shot.get("prompt") or "")
    shares = {str(p.get("name") or ""): float(p.get("占比") or 0)
              for p in parsed["people"]}
    facings = {str(p.get("name") or ""): str(p.get("朝向") or "")
               for p in parsed["people"]}

    cast = []
    for cid in cast_ids:
        c = chars.get(cid) or {}
        name = c.get("name") or cid
        cast.append({"character_id": cid, "name": name,
                     "refs": overrides.get(cid) or db.loads(c.get("refs_json", "[]"), []),
                     "facing": facings.get(name, ""), "share": shares.get(name, 0)})

    scene: dict = {}
    if shot.get("scene_id"):
        with db.txn() as conn:
            row = conn.execute(
                "SELECT plate_path, extra_plates_json FROM scenes "
                "WHERE project_id=? AND scene_id=?",
                (project_id, shot["scene_id"])).fetchone()
        if row:
            scene = {"plate_path": row["plate_path"] or "",
                     "extra_plates": db.loads(row["extra_plates_json"], [])}

    plan_shot = dict(shot)
    plan_shot["camera_slots"] = parsed["camera"]
    return refs_mod.plan_refs(plan_shot, cast, scene)


def save_shots(project_id: str, shots: list[dict]) -> list[dict]:
    """Replace the whole shot list for a project, then recompute fingerprints.

    Simpler and safer than per-field diffing: `ordinal` is derived from list
    order, and `shot_id` stays the identity.
    """
    now = db.now()
    with db.txn() as conn:
        existing_rows = {r["shot_id"]: dict(r) for r in conn.execute(
            "SELECT * FROM shots WHERE project_id=?", (project_id,)).fetchall()}
        existing = set(existing_rows)
        keep = set()
        for i, s in enumerate(shots):
            sid = str(s.get("shot_id") or f"s{i + 1:02d}")
            prev = existing_rows.get(sid, {})
            # a partial update must not silently wipe anchor/dialogue/duration
            def pick(key: str, default):
                v = s.get(key)
                return prev.get(key, default) if v is None else v
            keep.add(sid)
            cast_list = (s.get("cast") if s.get("cast") is not None
                         else db.loads(prev.get("cast_json", "[]"), []))
            anchor_val = pick("anchor", "")
            # 有人物时锚定母版是错的（会把人物做成母版首帧）；服务端强制归零，
            # 这样前端不管怎么误操作都不会出现「人物 + plate」的坏状态
            if cast_list and anchor_val == "plate":
                anchor_val = ""
            conn.execute(
                """INSERT INTO shots(project_id,shot_id,ordinal,scene_id,cast_json,camera,prompt,
                                     dialogue,sound,music,transition,anchor,duration,first_frame,
                                     last_frame,chain_ref,continuation,fingerprint,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,shot_id) DO UPDATE SET
                     ordinal=excluded.ordinal, scene_id=excluded.scene_id, cast_json=excluded.cast_json,
                     camera=excluded.camera, prompt=excluded.prompt, dialogue=excluded.dialogue,
                     sound=excluded.sound, music=excluded.music,
                     transition=excluded.transition, anchor=excluded.anchor,
                     duration=excluded.duration, first_frame=excluded.first_frame,
                     last_frame=excluded.last_frame,
                     chain_ref=excluded.chain_ref,
                     continuation=excluded.continuation, updated_at=excluded.updated_at""",
                (project_id, sid, i,
                 pick("scene_id", ""),
                 json.dumps(s.get("cast") if s.get("cast") is not None
                            else db.loads(prev.get("cast_json", "[]"), []), ensure_ascii=False),
                 pick("camera", ""), pick("prompt", ""),
                 pick("dialogue", ""), pick("sound", ""), pick("music", ""),
                 pick("transition", "cut"), anchor_val,
                 float(pick("duration", 5.0) or 5.0), pick("first_frame", ""),
                 pick("last_frame", ""),
                 1 if int(pick("chain_ref", 0) or 0) else 0,
                 str(pick("continuation", "cut")), "", now))
        for sid in existing - keep:
            conn.execute("DELETE FROM shots WHERE project_id=? AND shot_id=?", (project_id, sid))
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        db.emit(conn, project_id, "", "shots.saved", {"count": len(shots)})
    recompute_fingerprints(project_id)
    return list_shots(project_id)


def set_chain_ref(project_id: str, shot_id: str, enabled: bool) -> dict:
    """开关「接上一镜末帧（人物镜）」。

    人物镜走 H3CastToVideo，它没有 first_frame 输入；开启后把上一镜的末帧
    追加到 scene_images 里，作为视觉锚点，比纯 cut 更接近连续。
    """
    with db.txn() as conn:
        cur = conn.execute(
            "UPDATE shots SET chain_ref=?, updated_at=? WHERE project_id=? AND shot_id=?",
            (1 if enabled else 0, db.now(), project_id, shot_id))
        if cur.rowcount == 0:
            raise ValueError("镜头不存在")
        db.emit(conn, project_id, shot_id, "shot.chain_ref", {"enabled": bool(enabled)})
    recompute_fingerprints(project_id)
    return list_shots(project_id)


def recompute_fingerprints(project_id: str, params: dict | None = None) -> list[dict]:
    """Recompute fingerprints left-to-right so chain shots fold in their predecessor."""
    shots = list_shots(project_id)
    scenes = {s["scene_id"]: s for s in list_scenes(project_id)}
    prev_fp = ""
    updated = []
    for sh in shots:
        refs = cast_refs(sh["cast"])
        # 槽位计划：用哪几张图、什么顺序（决定出片，必须进指纹）
        plan = ref_plan_for(project_id, sh)
        sig = refs_mod.plan_signature(plan)
        fp = shot_fingerprint(sh, scenes.get(sh["scene_id"]), refs, params, prev_fp, sig)
        plan_json = json.dumps(plan, ensure_ascii=False)
        if fp != sh["fingerprint"] or plan_json != (sh.get("ref_plan_json") or "{}"):
            with db.txn() as conn:
                conn.execute(
                    "UPDATE shots SET fingerprint=?, ref_plan_json=? "
                    "WHERE project_id=? AND shot_id=?",
                    (fp, plan_json, project_id, sh["shot_id"]))
        prev_fp = fp
        sh["fingerprint"] = fp
        sh["ref_plan"] = plan
        updated.append(sh)
    return updated


def shots_to_render(project_id: str, wanted: list[str] | None = None) -> list[str]:
    """过滤出真正需要渲的镜。

    跳过两类：
      · 已有片段且指纹一致（clean / review / passed / rejected 都算"渲过了"）
      · 最近连续失败 2 次以上（避免一夜里反复撞同一个错）
    失效（stale）和从未渲过的镜照常返回。
    """
    shots = {s["shot_id"]: s for s in recompute_fingerprints(project_id)}
    order = [s["shot_id"] for s in list_shots(project_id)]
    want = [s for s in (wanted or order) if s in shots]
    out: list[str] = []
    with db.txn() as conn:
        for sid in want:
            take = conn.execute(
                """SELECT fingerprint FROM takes WHERE project_id=? AND shot_id=?
                   ORDER BY version DESC LIMIT 1""", (project_id, sid)).fetchone()
            if take is not None and (take["fingerprint"] or "") == (shots[sid].get("fingerprint") or ""):
                continue                      # 已渲且没改稿
            fails = conn.execute(
                """SELECT COUNT(*) FROM jobs WHERE project_id=? AND kind='render'
                   AND state='failed' AND payload LIKE ?""",
                (project_id, f'%"{sid}"%')).fetchone()[0]
            if fails >= 2 and take is None:
                continue                      # 从没成功过、且失败 2 次以上 → 先别烧卡
            out.append(sid)
    return out


def blocked_shots(project_id: str) -> list[str]:
    """连续失败 2 次以上、且还没有片段的镜（UI 可以显示"需要人工看"）。"""
    shots = [s["shot_id"] for s in list_shots(project_id)]
    out = []
    with db.txn() as conn:
        for sid in shots:
            take = conn.execute(
                "SELECT 1 FROM takes WHERE project_id=? AND shot_id=?", (project_id, sid)).fetchone()
            if take:
                continue
            fails = conn.execute(
                """SELECT COUNT(*) FROM jobs WHERE project_id=? AND kind='render'
                   AND state='failed' AND payload LIKE ?""",
                (project_id, f'%"{sid}"%')).fetchone()[0]
            if fails >= 2:
                out.append(sid)
    return out


def shot_state(project_id: str, shot: dict) -> dict:
    """Derive one shot's state from takes / reviews / jobs (never stored twice)."""
    with db.txn() as conn:
        take = conn.execute(
            """SELECT * FROM takes WHERE project_id=? AND shot_id=?
               ORDER BY version DESC LIMIT 1""",
            (project_id, shot["shot_id"])).fetchone()
        review = conn.execute(
            """SELECT * FROM reviews WHERE project_id=? AND shot_id=?
               ORDER BY id DESC LIMIT 1""",
            (project_id, shot["shot_id"])).fetchone()
        if take is not None and review is not None and review["version"] != take["version"]:
            # a review only judges the take it was made on
            review = None
        job = conn.execute(
            """SELECT * FROM jobs WHERE project_id=? AND state IN ('queued','running')
                 AND kind='render' AND payload LIKE ? ORDER BY created_at DESC LIMIT 1""",
            (project_id, f'%"{shot["shot_id"]}"%')).fetchone()
        preview_job = conn.execute(
            """SELECT * FROM jobs WHERE project_id=? AND state IN ('queued','running')
                 AND kind='preview' AND payload LIKE ? ORDER BY created_at DESC LIMIT 1""",
            (project_id, f'%"{shot["shot_id"]}"%')).fetchone()

    if job:
        state = job["state"]
    elif take is None:
        state = "empty"
    elif take["fingerprint"] != shot["fingerprint"]:
        state = "stale"
    elif review is None:
        state = "review"
    elif review["verdict"] == "pass":
        state = "passed"
    else:
        state = "rejected"

    d = db.row_to_dict(take) or {}
    if d:
        d["metrics"] = db.loads(d.pop("metrics_json", "{}") or "{}", {})
    rv = db.row_to_dict(review) or {}
    pv = get_preview(project_id, shot["shot_id"])
    last_job = None
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM jobs WHERE project_id=? AND payload LIKE ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, f'%"{shot["shot_id"]}"%')).fetchone()
        last_job = db.row_to_dict(row)
    return {
        "state": state,
        "take": d,
        "review": rv,
        "preview": pv,
        "job": db.row_to_dict(job) if job else None,
        "preview_job": db.row_to_dict(preview_job) if preview_job else None,
        "last_job": last_job,
    }


def reserve_take(project_id: str, shot_id: str) -> dict:
    """Allocate the next version + path without writing a row yet."""
    with db.txn() as conn:
        versions = [r["version"] for r in conn.execute(
            "SELECT version FROM takes WHERE project_id=? AND shot_id=?",
            (project_id, shot_id)).fetchall()]
    v = next_version(versions)
    return {"version": v, "path": str(take_path(project_id, shot_id, v))}


def record_take(project_id: str, shot_id: str, path: str, fingerprint: str,
                elapsed_s: float = 0.0, peak_mib: int = 0, note: str = "",
                version: int | None = None, metrics: dict | None = None,
                dialogue: str = "") -> dict:
    with db.txn() as conn:
        versions = [r["version"] for r in conn.execute(
            "SELECT version FROM takes WHERE project_id=? AND shot_id=?",
            (project_id, shot_id)).fetchall()]
        v = version if version else next_version(versions)
        conn.execute(
            """INSERT INTO takes(project_id,shot_id,version,path,fingerprint,elapsed_s,peak_mib,
                                 metrics_json,dialogue,created_at,note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (project_id, shot_id, v, path, fingerprint, elapsed_s, peak_mib,
             json.dumps(metrics or {}, ensure_ascii=False), dialogue, db.now(), note))
        db.emit(conn, project_id, shot_id, "take.recorded",
                {"version": v, "path": path, "elapsed_s": elapsed_s})
    return {"shot_id": shot_id, "version": v, "path": path}


def delete_take(project_id: str, shot_id: str, version: int | None = None) -> dict:
    """Remove a take (latest by default) and its file; reviews of it go too."""
    with db.txn() as conn:
        if version is None:
            row = conn.execute(
                """SELECT version, path FROM takes WHERE project_id=? AND shot_id=?
                   ORDER BY version DESC LIMIT 1""", (project_id, shot_id)).fetchone()
        else:
            row = conn.execute(
                """SELECT version, path FROM takes WHERE project_id=? AND shot_id=? AND version=?""",
                (project_id, shot_id, version)).fetchone()
        if row is None:
            raise ValueError("没有可删除的版本")
        conn.execute("DELETE FROM takes WHERE project_id=? AND shot_id=? AND version=?",
                     (project_id, shot_id, row["version"]))
        conn.execute("DELETE FROM reviews WHERE project_id=? AND shot_id=? AND version=?",
                     (project_id, shot_id, row["version"]))
        db.emit(conn, project_id, shot_id, "take.deleted",
                {"version": row["version"]})
    path = Path(row["path"])
    if path.is_file():
        path.unlink(missing_ok=True)
    return {"shot_id": shot_id, "deleted_version": row["version"],
            "remaining": [t["version"] for t in
                          (latest_takes(project_id, shot_id))]}


def latest_takes(project_id: str, shot_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute(
            """SELECT version, path FROM takes WHERE project_id=? AND shot_id=?
               ORDER BY version DESC""", (project_id, shot_id)).fetchall()
    return [dict(r) for r in rows]


def take_by_version(project_id: str, shot_id: str, version: int) -> dict | None:
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM takes WHERE project_id=? AND shot_id=? AND version=?""",
            (project_id, shot_id, version)).fetchone()
    return db.row_to_dict(row)


def adopt_take(project_id: str, shot_id: str, version: int | None = None) -> dict:
    """Declare a take valid for the shot's *current* inputs.

    version=None 表示最新一版；指定版本可把"旧但正确"的那版认回来，
    不用重渲（剧本改过之后文件还在，只是被标成失效）。
    """
    shots = recompute_fingerprints(project_id)
    sh = next((s for s in shots if s["shot_id"] == shot_id), None)
    if sh is None:
        raise ValueError(f"镜头不存在: {shot_id}")
    take = latest_take(project_id, shot_id) if version is None \
        else take_by_version(project_id, shot_id, version)
    if not take:
        raise ValueError("这一镜还没有片段" if version is None else f"没有 v{version}")
    with db.txn() as conn:
        conn.execute("UPDATE takes SET fingerprint=? WHERE project_id=? AND shot_id=? AND version=?",
                     (sh["fingerprint"], project_id, shot_id, take["version"]))
        conn.execute("""UPDATE takes SET chosen=0
                        WHERE project_id=? AND shot_id=?""", (project_id, shot_id))
        conn.execute("""UPDATE takes SET chosen=1
                        WHERE project_id=? AND shot_id=? AND version=?""",
                     (project_id, shot_id, take["version"]))
        db.emit(conn, project_id, shot_id, "take.adopted",
                {"version": take["version"]})
    return {"shot_id": shot_id, "version": take["version"],
            "state": shot_state(project_id, sh)["state"]}


def stale_of(project_id: str, shot: dict) -> bool:
    """该镜的最新片段是否与当前输入不符（已失效）。"""
    take = latest_take(project_id, shot["shot_id"])
    if not take:
        return False
    return (take.get("fingerprint") or "") != (shot.get("fingerprint") or "")


def all_takes(project_id: str, shot_id: str) -> list[dict]:
    """这一镜的全部版本（新→旧），带选用标记与"是否与当前剧本一致"。"""
    shot = next((s for s in recompute_fingerprints(project_id)
                 if s["shot_id"] == shot_id), None)
    fp = (shot or {}).get("fingerprint") or ""
    with db.txn() as conn:
        rows = conn.execute(
            """SELECT * FROM takes WHERE project_id=? AND shot_id=?
               ORDER BY version DESC""", (project_id, shot_id)).fetchall()
    out = []
    for r in rows:
        d = db.row_to_dict(r) or {}
        d["metrics"] = db.loads(d.pop("metrics_json", "{}") or "{}", {})
        d["chosen"] = bool(d.get("chosen"))
        d["matches_script"] = bool(fp) and (d.get("fingerprint") or "") == fp
        out.append(d)
    return out


def choose_take(project_id: str, shot_id: str, version: int) -> dict:
    """把某一版指定为这一镜的"选用版"（合成用它）。"""
    take = take_by_version(project_id, shot_id, version)
    if not take:
        raise ValueError(f"没有 v{version}")
    with db.txn() as conn:
        conn.execute("UPDATE takes SET chosen=0 WHERE project_id=? AND shot_id=?",
                     (project_id, shot_id))
        conn.execute("""UPDATE takes SET chosen=1
                        WHERE project_id=? AND shot_id=? AND version=?""",
                     (project_id, shot_id, version))
        db.emit(conn, project_id, shot_id, "take.chosen", {"version": version})
    return {"shot_id": shot_id, "version": version}


def chosen_take(project_id: str, shot_id: str) -> dict | None:
    """合成时用：优先"选用版"，没有就退到最新版。"""
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM takes WHERE project_id=? AND shot_id=? AND chosen=1
               ORDER BY version DESC LIMIT 1""", (project_id, shot_id)).fetchone()
    return db.row_to_dict(row) if row else latest_take(project_id, shot_id)


def choose_latest_all(project_id: str) -> dict:
    """批量：每镜都选最新一版。"""
    shots = recompute_fingerprints(project_id)
    picked = []
    for sh in shots:
        take = latest_take(project_id, sh["shot_id"])
        if take:
            choose_take(project_id, sh["shot_id"], take["version"])
            picked.append({"shot_id": sh["shot_id"], "version": take["version"]})
    return {"chosen": picked}


def pass_all(project_id: str) -> dict:
    """批量通过：有片段的镜头一次性标成 passed（不改指纹）。"""
    shots = recompute_fingerprints(project_id)
    marked = []
    for sh in shots:
        take = chosen_take(project_id, sh["shot_id"])
        if not take:
            continue
        with db.txn() as conn:
            conn.execute("""UPDATE takes SET fingerprint=?
                            WHERE project_id=? AND shot_id=? AND version=?""",
                         (sh["fingerprint"], project_id, sh["shot_id"], take["version"]))
        marked.append(sh["shot_id"])
    return {"passed": marked}


BEDS_DIR = db.DATA_DIR / "beds"


def bed_path(project_id: str, kind: str, variant: str = "auto") -> Path:
    """整集音频床文件位置（bgm / ambience）。

    variant:
      auto   —— 模型生成的那条（点「生成」写的）
      custom —— 用户上传/设为 BGM 的那条
    老版本只有一个 <pid>_<kind>.m4a，保留 legacy_bed_path() 做兼容。
    """
    BEDS_DIR.mkdir(parents=True, exist_ok=True)
    return BEDS_DIR / f"{project_id}_{kind}_{variant}.m4a"


def legacy_bed_path(project_id: str, kind: str) -> Path:
    """旧版本的单文件路径（<pid>_<kind>.m4a），只用于迁移判断。"""
    return BEDS_DIR / f"{project_id}_{kind}.m4a"


def delete_beds(project_id: str, kind: str) -> dict:
    """删掉这一条床的所有变体（auto / custom / 旧单文件）。"""
    removed = []
    for path in (bed_path(project_id, kind, "auto"),
                 bed_path(project_id, kind, "custom"),
                 legacy_bed_path(project_id, kind)):
        if path.is_file():
            path.unlink()
            removed.append(str(path))
    return {"removed": removed}


def resolve_bed(project_id: str, kind: str) -> tuple[str, dict]:
    """按项目配置算出这一条床实际用哪个文件。

    返回 (路径, 报告)。路径为空表示"不铺"或"还没准备好"，
    报告里一定写清原因——不静默跳过。
    """
    aud = (get_project(project_id) or {}).get("audio") or {}
    cfg = aud.get(kind) or {}
    mode = (cfg.get("mode") or "off").lower()
    if mode in ("", "off", "none"):
        return "", {"mode": "off", "applied": False, "reason": "配置为不铺"}
    path = ""
    if mode == "file":
        path = cfg.get("path") or ""
        if not path:
            cand = bed_path(project_id, kind, "custom")
            if not cand.is_file():
                cand = legacy_bed_path(project_id, kind)   # 老版本上传的文件
            path = str(cand) if cand.is_file() else ""
        if not path:
            return "", {"mode": mode, "applied": False,
                        "reason": "选了自定义文件，但还没上传"}
    elif mode == "auto":
        # 只认模型生成的那条；旧的单文件 <pid>_<kind>.m4a 算"自定义"，
        # 不能混用（否则切回"自动"还在放用户上传的音乐）
        cand = bed_path(project_id, kind, "auto")
        path = str(cand) if cand.is_file() else ""
        if not path:
            return "", {"mode": mode, "applied": False,
                        "reason": "还没生成模型音频（点「生成」）"}
    else:
        return "", {"mode": mode, "applied": False, "reason": f"未知模式 {mode}"}
    if not Path(path).is_file():
        return "", {"mode": mode, "applied": False, "reason": "文件不存在"}
    return path, {"mode": mode, "applied": True, "path": path,
                  "gain_db": cfg.get("gain_db"),
                  "duck_under_voice": bool(cfg.get("duck_under_voice"))}


def assemble(project_id: str, width: int = 1344, height: int = 768,
             with_subs: bool = True, allow_stale: bool = False,
             shot_ids: list[str] | None = None) -> dict:
    """Stitch every shot's latest take into one film (no GPU model involved).

    allow_stale：镜头的剧本/资产改过之后旧片段会标成"已失效"，但文件还在。
    合成时勾选这一项就照用（画面可能和现在的剧本对不上，由人决定）。
    shot_ids：只合成指定镜头（按项目内顺序），用于批量选片合成。
    """
    import subprocess
    shots = recompute_fingerprints(project_id)
    if not shots:
        raise ValueError("没有镜头")
    if shot_ids:
        wanted = {sid for sid in shot_ids if sid}
        shots = [s for s in shots if s["shot_id"] in wanted]
        if not shots:
            raise ValueError("选中的镜头为空")
    missing = [s["shot_id"] for s in shots if not latest_take(project_id, s["shot_id"])]
    if missing:
        raise ValueError(f"这些镜头还没有片段: {', '.join(missing)}")

    # 合成不拦"失效"：有片段就拼，用哪一版由人决定
    stale = [s["shot_id"] for s in shots
             if s.get("fingerprint") and stale_of(project_id, s)]
    used = []

    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = db.DATA_DIR / "projects" / project_id
    staging = root / "assemble" / stamp
    staging.mkdir(parents=True, exist_ok=True)
    used: list[dict] = []
    for i, sh in enumerate(shots, start=1):
        take = chosen_take(project_id, sh["shot_id"])
        link = staging / f"{i:04d}.mp4"
        if link.exists():
            link.unlink()
        link.symlink_to(take["path"])
        used.append({"shot_id": sh["shot_id"], "version": take.get("version")})

    out_dir = root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = out_dir / f"{project_id}_{stamp}.mp4"
    merged_raw = out_dir / f"{project_id}_{stamp}_merge.mp4"
    # 整集音频床：模型逐镜不生成 BGM，这里统一铺两条
    audio_report: dict = {}
    merge_args = [str(VENV_PY), "scripts/merge.py", "--segments", str(staging),
                  "--out", str(merged_raw),
                  "--width", str(width), "--height", str(height)]
    aud_cfg = (get_project(project_id) or {}).get("audio") or {}
    for kind, flag, gain_flag, default_gain in (
            ("bgm", "--bgm", "--bgm-gain", -18.0),
            ("ambience", "--ambience", "--ambience-gain", -26.0)):
        path, rep = resolve_bed(project_id, kind)
        if path:
            gain = rep.get("gain_db")
            gain = float(gain) if gain is not None else default_gain
            merge_args += [flag, path, gain_flag, str(gain)]
            if kind == "bgm" and (aud_cfg.get("bgm") or {}).get("duck_under_voice"):
                merge_args += ["--voice-duck"]
        audio_report[kind] = rep
    cp = subprocess.run(merge_args,
                        cwd=str(REPO_DIR), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0 or not merged_raw.is_file():
        raise RuntimeError((cp.stdout or "")[-400:])

    final = merged_raw
    dialogues = [s for s in shots if (s.get("dialogue") or "").strip()]
    if with_subs and dialogues:
        from ..exec import render_adapter
        script_path = render_adapter.write_script(project_id, width, height)
        # 字幕用选用版的台词（音频是那次渲染的 prompt，字幕必须跟它一致）
        try:
            _sp = json.loads(Path(script_path).read_text(encoding="utf-8"))
            for _i, _sh in enumerate(_sp.get("shots") or []):
                _t = chosen_take(project_id, _sh.get("shot_id"))
                if _t and (_t.get("dialogue") or "").strip():
                    _sh["dialogue"] = _t["dialogue"]
            Path(script_path).write_text(json.dumps(_sp, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        cp2 = subprocess.run([str(VENV_PY), "scripts/finalize.py", str(script_path),
                              "--in", str(merged_raw), "--out", str(merged),
                              "--segments", str(staging),
                              "--width", str(width), "--height", str(height)],
                             cwd=str(REPO_DIR), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        if cp2.returncode == 0 and merged.is_file():
            final = merged

    return {"out": str(final), "url": f"/outputs/{project_id}/{final.name}",
            "shots": len(shots), "subtitles": bool(dialogues) and with_subs,
            "audio": audio_report, "stale_used": stale, "versions": used}


def output_file(project_id: str, name: str) -> Path:
    p = db.DATA_DIR / "projects" / project_id / "output" / Path(name).name
    if not p.is_file():
        raise ValueError("文件不存在")
    return p


def latest_take(project_id: str, shot_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM takes WHERE project_id=? AND shot_id=?
               ORDER BY version DESC LIMIT 1""", (project_id, shot_id)).fetchone()
    return db.row_to_dict(row)


def record_preview(project_id: str, shot_id: str, path: str, fingerprint: str) -> dict:
    with db.txn() as conn:
        conn.execute(
            """INSERT INTO previews(project_id,shot_id,path,fingerprint,created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(project_id,shot_id) DO UPDATE SET
                 path=excluded.path, fingerprint=excluded.fingerprint,
                 created_at=excluded.created_at""",
            (project_id, shot_id, path, fingerprint, db.now()))
        db.emit(conn, project_id, shot_id, "preview.recorded", {"path": path})
    return {"shot_id": shot_id, "path": path}


def get_preview(project_id: str, shot_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM previews WHERE project_id=? AND shot_id=?",
                           (project_id, shot_id)).fetchone()
    return db.row_to_dict(row)


def review_shot(project_id: str, shot_id: str, verdict: str, reason: str = "") -> dict:
    if verdict not in ("pass", "reject"):
        raise ValueError("verdict must be pass or reject")
    with db.txn() as conn:
        take = conn.execute(
            """SELECT version FROM takes WHERE project_id=? AND shot_id=?
               ORDER BY version DESC LIMIT 1""", (project_id, shot_id)).fetchone()
        if take is None:
            raise ValueError("该镜还没有任何 take，无法审")
        conn.execute(
            """INSERT INTO reviews(project_id,shot_id,version,verdict,reason,created_at)
               VALUES (?,?,?,?,?,?)""",
            (project_id, shot_id, take["version"], verdict, reason, db.now()))
        db.emit(conn, project_id, shot_id, "shot.reviewed",
                {"verdict": verdict, "reason": reason, "version": take["version"]})
    return {"shot_id": shot_id, "verdict": verdict}


def batch_review(project_id: str, shot_ids: list[str], verdict: str,
                 reason: str = "") -> dict:
    """批量审核：一次通过/驳回多个镜头。

    没有 take 的镜头跳过（不阻断整批），返回 done / skipped 两个清单。
    """
    if verdict not in ("pass", "reject"):
        raise ValueError("verdict must be pass or reject")
    ids = [s for s in (shot_ids or []) if s]
    if not ids:
        raise ValueError("没有选中镜头")
    known = {s["shot_id"] for s in list_shots(project_id)}
    done, skipped = [], []
    for sid in ids:
        if sid not in known:
            skipped.append(sid)
            continue
        try:
            review_shot(project_id, sid, verdict, reason)
            done.append(sid)
        except ValueError:
            skipped.append(sid)       # 没有 take 的镜头
    return {"ok": True, "verdict": verdict, "done": done,
            "skipped": skipped, "count": len(done)}


def stats(project_id: str) -> dict:
    """Throughput / quality numbers a producer actually looks at."""
    shots = recompute_fingerprints(project_id)
    states = [shot_state(project_id, s)["state"] for s in shots]
    with db.txn() as conn:
        takes = conn.execute(
            "SELECT shot_id, version, elapsed_s, peak_mib, note FROM takes WHERE project_id=?",
            (project_id,)).fetchall()
        reviews = conn.execute(
            "SELECT shot_id, verdict, reason FROM reviews WHERE project_id=?",
            (project_id,)).fetchall()

    per_shot_versions: dict[str, int] = {}
    for t in takes:
        per_shot_versions[t["shot_id"]] = max(per_shot_versions.get(t["shot_id"], 0),
                                              t["version"])
    rerendered = sum(1 for sid, v in per_shot_versions.items() if v > 1)

    elapsed = [t["elapsed_s"] for t in takes if t["elapsed_s"]]
    peaks = [t["peak_mib"] for t in takes if t["peak_mib"]]
    passed = [r for r in reviews if r["verdict"] == "pass"]
    first_take_pass = sum(1 for sid, v in per_shot_versions.items()
                          if v == 1 and sid in {r["shot_id"] for r in passed})
    reasons: dict[str, int] = {}
    for r in reviews:
        if r["verdict"] == "reject" and r["reason"]:
            reasons[r["reason"]] = reasons.get(r["reason"], 0) + 1

    return {
        "project_id": project_id,
        "shots": len(shots),
        "takes": len(takes),
        "rerendered_shots": rerendered,
        "rerender_rate": round(rerendered / len(shots), 3) if shots else 0,
        "passed": states.count("passed"),
        "pass_rate": round(states.count("passed") / len(shots), 3) if shots else 0,
        "first_take_pass": first_take_pass,
        "avg_shot_seconds": round(sum(elapsed) / len(elapsed), 1) if elapsed else 0,
        "total_gpu_minutes": round(sum(elapsed) / 60, 1),
        "peak_mib": max(peaks) if peaks else 0,
        "reject_reasons": reasons,
    }


def auto_transitions(project_id: str) -> dict:
    """chain only when the cast set is unchanged; otherwise cut.

    Chaining across a cast change makes the model keep the previous frame and
    barely move, so every shot looks the same — the most common "why is my film
    static" cause.
    """
    shots = list_shots(project_id)
    changed = 0
    for i, sh in enumerate(shots):
        if i == 0:
            want = "cut"
        else:
            same = sorted(prev_cast(shots[i - 1])) == sorted(sh["cast"] or [])
            want = "chain" if same else "cut"
        if sh["transition"] != want:
            sh["transition"] = want
            changed += 1
    save_shots(project_id, shots)
    return {"changed": changed,
            "order": [(s["shot_id"], s["transition"]) for s in list_shots(project_id)]}


def prev_cast(sh: dict) -> list[str]:
    return sh.get("cast") or []


def delete_shot(project_id: str, shot_id: str, delete_files: bool = True) -> dict:
    """Remove one shot (and its clips) from the project."""
    shots = list_shots(project_id)
    target = next((s for s in shots if s["shot_id"] == shot_id), None)
    if target is None:
        raise ValueError(f"镜头不存在: {shot_id}")
    takes = latest_takes(project_id, shot_id)
    with db.txn() as conn:
        conn.execute("DELETE FROM shots WHERE project_id=? AND shot_id=?", (project_id, shot_id))
        conn.execute("DELETE FROM takes WHERE project_id=? AND shot_id=?", (project_id, shot_id))
        conn.execute("DELETE FROM reviews WHERE project_id=? AND shot_id=?", (project_id, shot_id))
        conn.execute("DELETE FROM previews WHERE project_id=? AND shot_id=?", (project_id, shot_id))
        db.emit(conn, project_id, shot_id, "shot.deleted", {"takes": len(takes)})
    if delete_files:
        for t in takes:
            p = Path(t["path"])
            if p.is_file() and db.DATA_DIR in p.parents:
                p.unlink(missing_ok=True)
        ff = Path(target.get("first_frame") or "")
        if ff.is_file() and db.DATA_DIR in ff.parents:
            ff.unlink(missing_ok=True)
        pv = get_preview(project_id, shot_id)
        if pv and Path(pv["path"]).is_file():
            Path(pv["path"]).unlink(missing_ok=True)
    recompute_fingerprints(project_id)
    return {"deleted": shot_id, "remaining": [s["shot_id"] for s in list_shots(project_id)]}


def delete_project(project_id: str) -> dict:
    """Remove a project with everything it owns (clips, assets, jobs)."""
    if not get_project(project_id):
        raise ValueError("项目不存在")
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events", "scenes"):
            conn.execute(f"DELETE FROM {t} WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
    root = db.DATA_DIR / "projects" / project_id
    if root.is_dir():
        import shutil
        shutil.rmtree(root, ignore_errors=True)
    return {"deleted": project_id}


def delete_character(cid: str, force: bool = False) -> dict:
    """Remove a character. Refuses while shots still cast it, unless forced."""
    chars = {c["character_id"]: c for c in list_characters()}
    cur = chars.get(cid)
    if not cur:
        raise ValueError("角色不存在")
    with db.txn() as conn:
        used = conn.execute(
            "SELECT project_id, shot_id, cast_json FROM shots WHERE cast_json LIKE ?",
            (f'%"{cid}"%',)).fetchall()
    users = [(r["project_id"], r["shot_id"]) for r in used]
    if users and not force:
        sample = ", ".join(f"{p}/{s}" for p, s in users[:5])
        raise ValueError(f"还有 {len(users)} 个镜头在用这个角色（{sample}）；先解除绑定，或强制删除")

    if force and users:
        for r in used:
            cast = [c for c in db.loads(r["cast_json"], []) if c != cid]
            with db.txn() as conn:
                conn.execute("UPDATE shots SET cast_json=? WHERE project_id=? AND shot_id=?",
                             (json.dumps(cast, ensure_ascii=False), r["project_id"], r["shot_id"]))
        recompute_fingerprints_multi({p for p, _ in users})

    with db.txn() as conn:
        conn.execute("DELETE FROM characters WHERE character_id=?", (cid,))
        db.emit(conn, "", "", "character.deleted", {"character_id": cid})
    for ref in cur.get("refs", []):
        p = Path(ref)
        # only delete files we own, never arbitrary paths from the DB
        if p.is_file() and db.DATA_DIR in p.parents:
            p.unlink(missing_ok=True)
    card = db.DATA_DIR / "characters" / f"{cid}.json"
    card.unlink(missing_ok=True)
    return {"deleted": cid, "unbound_from": len(users) if force else 0}


def recompute_fingerprints_multi(project_ids) -> None:
    for pid in project_ids:
        try:
            recompute_fingerprints(pid)
        except Exception:  # noqa: BLE001
            pass


def set_first_frame(project_id: str, shot_id: str, path: str) -> dict:
    """Pin one shot's first frame to a specific image (overrides chain/anchor)."""
    shots = list_shots(project_id)
    target = next((s for s in shots if s["shot_id"] == shot_id), None)
    if target is None:
        raise ValueError(f"镜头不存在: {shot_id}")
    target["first_frame"] = path
    target["anchor"] = ""
    save_shots(project_id, shots)
    fresh = next(s for s in list_shots(project_id) if s["shot_id"] == shot_id)
    return {**fresh, "state": shot_state(project_id, fresh)["state"]}


def clear_first_frame(project_id: str, shot_id: str) -> dict:
    return set_first_frame(project_id, shot_id, "")


def assign_shot(project_id: str, shot_id: str, *, cast_add: list[str] | None = None,
                cast_remove: list[str] | None = None, scene_id: str | None = None,
                transition: str | None = None) -> dict:
    """Targeted edit used by drag-and-drop; recomputes fingerprints afterwards."""
    shots = list_shots(project_id)
    target = next((s for s in shots if s["shot_id"] == shot_id), None)
    if target is None:
        raise ValueError(f"镜头不存在: {shot_id}")
    cast = list(target["cast"])
    for cid in cast_add or []:
        if cid not in cast:
            cast.append(cid)
    for cid in cast_remove or []:
        if cid in cast:
            cast.remove(cid)
    if len(cast) > 3:
        raise ValueError("每镜最多 3 个角色")
    changed_scene = scene_id is not None and scene_id != target["scene_id"]
    target["cast"] = cast
    if scene_id is not None:
        target["scene_id"] = scene_id
        # a scene drop re-anchors empty shots; a cast drop clears the anchor
        target["anchor"] = "plate" if (not cast and _scene_has_plate(project_id, scene_id)) else ""
    elif cast:
        target["anchor"] = ""
    if transition in ("cut", "chain"):
        target["transition"] = transition
    save_shots(project_id, shots)
    fresh = next(s for s in list_shots(project_id) if s["shot_id"] == shot_id)
    return {**fresh, "scene_changed": changed_scene,
            "state": shot_state(project_id, fresh)["state"]}


def _scene_has_plate(project_id: str, scene_id: str) -> bool:
    sc = get_scene(project_id, scene_id) or {}
    return bool(sc.get("plate_sha"))


def batch_update(project_id: str, shot_ids: list[str], *, scene_id: str | None = None,
                 transition: str | None = None, cast_add: list[str] | None = None,
                 cast_remove: list[str] | None = None,
                 chain_ref: bool | None = None) -> dict:
    """Apply the same edit to several shots, then recompute fingerprints once."""
    if not shot_ids:
        raise ValueError("没有选中镜头")
    shots = list_shots(project_id)
    by_id = {s["shot_id"]: s for s in shots}
    missing = [s for s in shot_ids if s not in by_id]
    if missing:
        raise ValueError(f"镜头不存在: {', '.join(missing)}")
    for sid in shot_ids:
        s = by_id[sid]
        cast = list(s["cast"])
        for cid in cast_add or []:
            if cid not in cast:
                cast.append(cid)
        for cid in cast_remove or []:
            if cid in cast:
                cast.remove(cid)
        if len(cast) > 3:
            raise ValueError(f"{sid}: 超过 3 个角色")
        s["cast"] = cast
        if scene_id is not None:
            s["scene_id"] = scene_id
            s["anchor"] = "plate" if (not cast and _scene_has_plate(project_id, scene_id)) else ""
        elif cast:
            s["anchor"] = ""
        if transition in ("cut", "chain"):
            s["transition"] = transition
        if chain_ref is not None:
            # 第一镜没有"上一镜"，强制关掉，免得白开一个无效开关
            s["chain_ref"] = (1 if chain_ref else 0) if s["ordinal"] > 0 else 0
    save_shots(project_id, shots)
    return {"updated": shot_ids, "count": len(shot_ids)}


def move_shot(project_id: str, shot_id: str, before_shot_id: str | None = None,
              to_end: bool = False) -> dict:
    """Reorder shots. Ordinal is presentation only, so fingerprints do not change.

    Chain is adjacency-sensitive, so we report any chain whose predecessor is no
    longer the shot it was bound to.
    """
    shots = list_shots(project_id)
    ids = [s["shot_id"] for s in shots]
    if shot_id not in ids:
        raise ValueError(f"镜头不存在: {shot_id}")
    by_id = {s["shot_id"]: s for s in shots}
    moved = by_id.pop(shot_id)
    rest = [s for s in shots if s["shot_id"] != shot_id]
    if to_end or not before_shot_id:
        rest.append(moved)
    else:
        if before_shot_id not in [s["shot_id"] for s in rest]:
            raise ValueError(f"目标镜头不存在: {before_shot_id}")
        idx = next(i for i, s in enumerate(rest) if s["shot_id"] == before_shot_id)
        rest.insert(idx, moved)

    warnings = []
    for i, s in enumerate(rest):
        if s["transition"] != "chain":
            continue
        if i == 0:
            warnings.append({"shot_id": s["shot_id"], "level": "error",
                             "msg": "chain 镜头被移到第一位，没有上游可接"})
            continue
        prev = rest[i - 1]
        if sorted(prev["cast"]) != sorted(s["cast"]):
            warnings.append({"shot_id": s["shot_id"], "level": "warn",
                             "msg": f"chain 的上游换成了 {prev['shot_id']}，角色集不同"})
    save_shots(project_id, rest)
    return {"order": [s["shot_id"] for s in rest], "warnings": warnings}
    sc = get_scene(project_id, scene_id) or {}
    return bool(sc.get("plate_sha"))

def preview_import(project_id: str, text: str) -> dict:
    """Diff an incoming script against what is already rendered, without writing.

    Returns which shots would become stale, so the UI can warn before the user
    throws away approved takes.
    """
    from . import script_import
    parsed = script_import.parse(text)
    current = {s["shot_id"]: s for s in list_shots(project_id)}
    scenes = {s["scene_id"]: s for s in list_scenes(project_id)}
    chars = {c["character_id"]: c for c in list_characters()}

    name_to_id = {c["name"]: c["character_id"] for c in chars.values()}
    for c in parsed["characters"]:
        name_to_id.setdefault(c["name"], c["character_id"])
    alias = _cast_aliases(parsed, name_to_id)

    changed, kept, added = [], [], []
    prev_fp = ""
    for s in parsed["shots"]:
        sid = s["shot_id"]
        cast_ids = _resolve_cast(s.get("cast") or [], name_to_id, alias)
        sc = scenes.get(s.get("scene_id", ""))
        new_fp = shot_fingerprint({**s, "cast": cast_ids}, sc,
                                  cast_refs(cast_ids), None, prev_fp)
        prev_fp = new_fp
        cur = current.get(sid)
        if cur is None:
            added.append(sid)
        elif cur["fingerprint"] == new_fp:
            kept.append(sid)
        else:
            changed.append(sid)
    incoming = {s["shot_id"] for s in parsed["shots"]}
    removed = [sid for sid in current if sid not in incoming]
    return {"shots": len(parsed["shots"]), "kept": kept, "changed": changed,
            "added": added, "removed": removed, "warnings": parsed["warnings"],
            "title": parsed["project"]["title"]}


def _resolve_characters(parsed: dict) -> dict[str, str]:
    """Map a script's character names to existing ids, creating only the missing.

    Without this, re-importing an exported script would mint a new `char_<hash>`
    for every name and orphan the existing character (and its refs).
    """
    existing = {c["name"]: c for c in list_characters()}
    by_name: dict[str, str] = {}
    for c in parsed["characters"]:
        name = c["name"]
        cur = existing.get(name)
        if cur:
            by_name[name] = cur["character_id"]
            want = (c.get("description") or "").strip()
            have = (cur.get("description") or "").strip()
            if want and want != have:
                # 剧本是角色卡的真相来源：不一致就以剧本为准（锁定的角色只报警）
                if cur.get("locked"):
                    parsed.setdefault("warnings", []).append(
                        f"角色「{name}」已锁定：剧本里的外观描述和库里不一致，未覆盖。")
                else:
                    upsert_character(cur["character_id"], name, want, cur.get("refs", []),
                                     species=c.get("species") or cur.get("species", ""))
                    parsed.setdefault("warnings", []).append(
                        f"角色「{name}」的外观描述已按剧本更新（之前库里是旧版本）。")
        else:
            upsert_character(c["character_id"], name, c.get("description", ""), c.get("refs", []),
                             species=c.get("species", ""))
            by_name[name] = c["character_id"]
    return by_name


def _cast_aliases(parsed: dict, name_to_id: dict[str, str]) -> dict[str, str]:
    """Map the script's own character ids onto existing ids when names match.

    A parsed shot may reference `char_<hash>` (the id the parser minted from the
    name) while the project already stores `c_demo`. Without this alias the cast
    silently switches to a character that does not exist.
    """
    alias = {}
    for c in parsed.get("characters", []):
        target = name_to_id.get(c["name"])
        if target and target != c["character_id"]:
            alias[c["character_id"]] = target
    return alias


def _resolve_cast(entries: list[str], name_to_id: dict[str, str],
                  alias: dict[str, str]) -> list[str]:
    out = []
    for e in entries or []:
        out.append(name_to_id.get(e) or alias.get(e) or e)
    return out


def import_markdown(project_id: str, text: str) -> dict:
    """Create/refresh a project from the Markdown script format.

    Existing takes are preserved; because fingerprints are recomputed afterwards,
    anything that no longer matches turns `stale` automatically.
    """
    from . import script_import
    parsed = script_import.parse(text)
    _existed = get_project(project_id) is not None
    if not _existed:
        create_project(project_id, parsed["project"]["title"], parsed["project"]["style"])
    else:
        with db.txn() as conn:
            conn.execute("UPDATE projects SET title=?, style=?, updated_at=? WHERE id=?",
                         (parsed["project"]["title"], parsed["project"]["style"],
                          db.now(), project_id))

    name_to_id = _resolve_characters(parsed)
    alias = _cast_aliases(parsed, name_to_id)

    for sc in parsed["scenes"]:
        cur = get_scene(project_id, sc["scene_id"]) or {}
        upsert_scene(project_id, sc["scene_id"], name=sc["name"] or cur.get("name", ""),
                     camera=sc["camera"] or cur.get("camera", ""),
                     props=sc["props"] or cur.get("props", ""),
                     light=sc["light"] or cur.get("light", ""),
                     plate_path=cur.get("plate_path", ""))

    shots = []
    for s in parsed["shots"]:
        # cast names -> existing character ids (never mint duplicates on re-import)
        shots.append({**s, "cast": _resolve_cast(s.get("cast") or [], name_to_id, alias)})
    save_shots(project_id, shots)
    return {
        "project_id": project_id,
        "updated": _existed,
        "title": parsed["project"]["title"],
        "characters": [c["name"] for c in parsed["characters"]],
        "scenes": [s["name"] for s in parsed["scenes"]],
        "shots": [s["shot_id"] for s in parsed["shots"]],
        "warnings": parsed["warnings"],
    }


# -------------------------------------------------------------------- plan

# plan_id -> (project_id, shot_ids, draft, created_at); lets confirm re-validate
# exactly the selection the user saw, instead of assuming "all shots"
PLAN_STORE: dict[str, dict] = {}
PLAN_TTL_S = 600


def plan(project_id: str, shot_ids: list[str] | None = None, draft: bool = False,
         force: bool = False) -> dict:
    all_shots = recompute_fingerprints(project_id)
    shots = all_shots
    if shot_ids:
        want = set(shot_ids)
        shots = [s for s in all_shots if s["shot_id"] in want]
    order_index = {s["shot_id"]: i for i, s in enumerate(all_shots)}
    scenes = {s["scene_id"]: s for s in list_scenes(project_id)}

    warnings: list[dict] = []
    todo: list[dict] = []

    # 项目级风格串如果混进角色服装，会污染每一镜（模型给每套衣服画一个人）
    project = get_project(project_id) or {}
    style = project.get("style") or ""
    costume_hits = [w for w in ("robe", "jacket", "shirt", "coat", "turtleneck",
                                "官袍", "绸袍", "短打", "长衫", "战袍", "西装")
                    if w in style.lower()]
    if costume_hits:
        warnings.append({"shot_id": "", "level": "warn",
                         "msg": "项目的「视觉语言」里混进了角色服装（"
                                + "、".join(costume_hits[:4])
                                + "），每镜会多画出人物；服装只该写在角色卡里"})
    chars_by_id = {c["character_id"]: c["name"] for c in list_characters()}
    for sh in shots:
        st = shot_state(project_id, sh)
        take = st.get("take") or {}
        is_final_take = bool(take) and (take.get("note") or "") != "draft"
        # a draft take must not block the final render; a final take does
        if st["state"] == "passed" and not force:
            continue
        if st["state"] == "review" and is_final_take and not draft and not force:
            continue
        if st["state"] == "review" and draft:
            continue
        sc = scenes.get(sh["scene_id"]) or {}
        if not sc:
            warnings.append({"shot_id": sh["shot_id"], "level": "error",
                             "msg": f"场景 {sh['scene_id'] or '(未设)'} 不存在（也没有母版图）"})
        elif not sc.get("plate_sha"):
            warnings.append({"shot_id": sh["shot_id"], "level": "warn",
                             "msg": "场景没有母版图，环境可能漂移"})
        if sh["cast"] and len(sh["cast"]) > 3:
            warnings.append({"shot_id": sh["shot_id"], "level": "error",
                             "msg": f"{len(sh['cast'])} 个角色，上限 3"})
        if sh["transition"] == "chain" and not sh.get("first_frame"):
            pos = order_index.get(sh["shot_id"], 0)
            prev = all_shots[pos - 1] if pos > 0 else None
            if prev is None:
                warnings.append({"shot_id": sh["shot_id"], "level": "error",
                                 "msg": "chain 是第一镜，没有上游可接"})
            else:
                if sorted(prev_cast(prev)) != sorted(sh["cast"] or []):
                    warnings.append({"shot_id": sh["shot_id"], "level": "warn",
                                     "msg": "出场角色和上一镜不同，chain 会让画面几乎不变，建议改 cut"})
                if (not latest_take(project_id, prev["shot_id"])
                        and prev["shot_id"] not in {s["shot_id"] for s in todo}):
                    warnings.append({"shot_id": sh["shot_id"], "level": "error",
                                     "msg": f"chain 的上游 {prev['shot_id']} 还没有片段，先渲它"})
        # 台词说话人必须在本镜出场角色里，否则说话人对不上（口型/视线会乱）
        dlg = (sh.get("dialogue") or "").replace("<d>", "").replace("</d>", "")
        if dlg:
            speaker = dlg.split("：")[0].split(":")[0].strip()
            cast_names = {chars_by_id.get(cid, "") for cid in sh["cast"]}
            if speaker and speaker not in cast_names:
                warnings.append({"shot_id": sh["shot_id"], "level": "warn",
                                 "msg": f"台词是「{speaker}」说的，但它不在本镜出场角色里"})
        if sh["anchor"] == "plate" and sc and not sc.get("plate_sha"):
            warnings.append({"shot_id": sh["shot_id"], "level": "error",
                             "msg": "设置了 anchor=plate 但场景没有母版图"})
        chars = len(sh["dialogue"].replace("<d>", "").replace("</d>", ""))
        if chars > DIALOGUE_MAX_CHARS:
            warnings.append({"shot_id": sh["shot_id"], "level": "warn",
                             "msg": f"台词 {chars} 字，5 秒读不完（上限 {DIALOGUE_MAX_CHARS}）"})
        todo.append({"shot_id": sh["shot_id"], "state": st["state"],
                     "fingerprint": sh["fingerprint"], "scene_id": sh["scene_id"]})

    per = MIN_PER_SHOT_DRAFT if draft else MIN_PER_SHOT_FULL
    minutes = round(len(todo) * per, 1)
    # worst case: the renderer retries a failing shot up to 3 times
    worst = round(len(todo) * per * 3, 1)
    plan_id = canonical_plan_id(project_id, todo, draft)
    PLAN_STORE[plan_id] = {"project_id": project_id, "shot_ids": shot_ids,
                           "draft": draft, "force": force, "created": time.time()}
    for old in [k for k, v in PLAN_STORE.items()
                if time.time() - v["created"] > PLAN_TTL_S]:
        PLAN_STORE.pop(old, None)
    # 契约校验：任何 error 都阻断渲染（避免渲出废片）
    try:
        from . import validate as _validate
        _rep = _validate.validate_project(project_id)
        warnings.extend(_rep["warnings"])
        warnings.extend(_rep["errors"])
    except Exception:  # noqa: BLE001  校验器不该阻断计划本身
        pass

    return {
        "plan_id": plan_id,
        "project_id": project_id,
        "draft": draft,
        "shots": todo,
        "count": len(todo),
        "est_minutes": minutes,
        "worst_minutes": worst,
        "est_finish": time.strftime("%H:%M", time.localtime(time.time() + minutes * 60)),
        "warnings": warnings,
        "blocking": [w for w in warnings if w["level"] == "error"],
    }


def canonical_plan_id(project_id: str, todo: list[dict], draft: bool) -> str:
    import hashlib
    raw = json.dumps({"p": project_id, "s": todo, "d": draft},
                     sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# -------------------------------------------------------------------- jobs


def active_job_for_shot(project_id: str, shot_id: str) -> dict | None:
    """该镜是否已经有排队/渲染中的任务（防止同一镜被重复排队）。"""
    with db.txn() as conn:
        row = conn.execute(
            """SELECT * FROM jobs WHERE project_id=? AND state IN ('queued','running')
                 AND payload LIKE ? ORDER BY created_at DESC LIMIT 1""",
            (project_id, f'%"{shot_id}"%')).fetchone()
    return db.row_to_dict(row)


def enqueue_job(project_id: str, kind: str, payload: dict) -> dict:
    jid = uuid.uuid4().hex[:12]
    with db.txn() as conn:
        conn.execute(
            "INSERT INTO jobs(id,project_id,kind,payload,state,created_at) VALUES (?,?,?,?,?,?)",
            (jid, project_id, kind, json.dumps(payload, ensure_ascii=False), "queued", db.now()))
        db.emit(conn, project_id, "", "job.enqueued", {"job_id": jid, "kind": kind})
    return get_job(jid)


def get_job(jid: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (jid,)).fetchone()
    return db.row_to_dict(row)


def list_jobs(project_id: str | None = None, limit: int = 50) -> list[dict]:
    with db.txn() as conn:
        if project_id:
            rows = conn.execute("SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC LIMIT ?",
                                (project_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                                (limit,)).fetchall()
    return [dict(r) for r in rows]


REPO_DIR = Path(__file__).resolve().parents[2]
_VENV = REPO_DIR / ".venv/bin/python"
VENV_PY = _VENV if _VENV.is_file() else Path(sys.executable)


def fail_orphan_running(reason: str = "服务重启，任务中断") -> int:
    """Clean up jobs left in 'running' by a dead process.

    夜间无人值守要能续跑：被重启/断电打断的批次如果还有重试额度，就重新排回去。
    注意：重新入队必须在事务**外面**做 —— enqueue_job 自己会开一个事务，
    在已打开的事务里再开会导致 SQLite 锁住，整个清理函数失败，队列就永远卡在 running。
    """
    to_requeue: list[tuple[str, str, dict]] = []
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE state='running'").fetchall()
        for row in rows:
            payload = db.loads(row["payload"], {})
            left = int(payload.get("retries_left", 0) or 0)
            shots = payload.get("shots") or []
            if left > 0 and shots:
                to_requeue.append((row["project_id"], row["kind"],
                                   {**payload, "shots": shots,
                                    "retries_left": left,       # 重启不算失败，不扣额度
                                    "auto_retry": True,
                                    "requeued_after": reason}))
                db.emit(conn, row["project_id"], "", "job.requeued",
                        {"job_id": row["id"], "shots": shots, "reason": reason})
        cur = conn.execute(
            "UPDATE jobs SET state='failed', error=?, finished_at=? WHERE state='running'",
            (reason, db.now()))
        n = cur.rowcount

    for pid, kind, payload in to_requeue:
        try:
            enqueue_job(pid, kind, payload)
        except Exception as e:  # noqa: BLE001
            print(f"[executor] 续排失败 {pid} {payload.get('shots')}: {e}", flush=True)
    return n

def cancel_job(job_id: str, reason: str = "cancelled") -> dict:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("任务不存在")
        if row["state"] not in ("queued", "running", "paused"):
            raise ValueError(f"任务状态 {row['state']}，不能取消")
        conn.execute("UPDATE jobs SET state='cancelled', error=?, finished_at=? WHERE id=?",
                     (reason, db.now(), job_id))
        db.emit(conn, row["project_id"], "", "job.cancelled", {"job_id": job_id})
    return get_job(job_id)


def cancel_project_jobs(project_id: str) -> dict:
    with db.txn() as conn:
        rows = conn.execute(
            "SELECT id, state FROM jobs WHERE project_id=? "
            "AND state IN ('queued','running','paused')", (project_id,)).fetchall()
    ids = [r["id"] for r in rows]
    killed = []
    for r in rows:
        if r["state"] == "running":
            killed.append(r["id"])
    return {"cancelled": len(ids), "ids": ids, "running": killed}


def take_path(project_id: str, shot_id: str, version: int) -> Path:
    root = Path.home() / "openvideo/studio-data" / "projects" / project_id / "takes"
    root.mkdir(parents=True, exist_ok=True)
    return root / take_name(shot_id, version)
