"""资产工坊候选图 —— 纯 DB CRUD + 入库（写进现有角色图集 / 场景母版目录）。

M1 阶段：生成接口还没接真实 H3-Image 工作流，candidates.path 会是空串或占位。
入库函数（adopt_candidate）把 candidates 里的图复制到目标目录，然后调 service 层更新引用。
"""

from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import db, service


def _row_to_candidate(row: db.sqlite3.Row) -> dict:
    c = {k: row[k] for k in row.keys()}
    c["url"] = _candidate_url(c.get("path", ""))
    return c


def _candidate_url(path: str) -> str:
    """把候选图的绝对路径换成 /assets/generated/... 直链；不在该目录就返回空串。"""
    if not path:
        return ""
    try:
        rel = Path(path).resolve().relative_to((db.DATA_DIR / "generated").resolve())
    except (ValueError, OSError):
        return ""
    return "/assets/generated/" + rel.as_posix()


def list_candidates(limit: int = 100) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute(
            "SELECT * FROM asset_candidates ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_candidate(r) for r in rows]


def list_outputs(project_id: str) -> list[dict]:
    """合成成片：projects/<pid>/output/*.mp4。

    同一时间戳会同时存在带字幕终版和 _merge 中间文件，只保留终版；
    中间文件在没有终版时才展示。
    """
    out_dir = db.DATA_DIR / "projects" / project_id / "output"
    if not out_dir.is_dir():
        return []
    picked: dict[str, Path] = {}
    for f in out_dir.glob("*.mp4"):
        if f.name.endswith("_merge.mp4"):
            picked.setdefault(f.name[: -len("_merge.mp4")], f)
        else:
            picked[f.stem] = f
    items = []
    for f in picked.values():
        st = f.stat()
        items.append({
            "name": f.name,
            "url": f"/outputs/{project_id}/{f.name}",
            "poster": f"/assets/output-poster/{project_id}/{f.name}",
            "path": str(f.resolve()),
            "size": st.st_size,
            "mtime": int(st.st_mtime),
            "project_id": project_id,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


# ---------------------------------------------------------------- 资产管理页目录

def catalog(project_id: str = "", group_id: str = "") -> dict:
    """按项目 / 分组筛选角色与场景，给「角色和场景」页用。

    项目优先于分组；都不传 = 所有未归档项目。
    角色是全局库，筛选时只保留在该项目（或该组项目）里用到的角色。
    """
    all_projects = service.list_projects(include_archived=True)
    by_id = {p["id"]: p for p in all_projects}
    if project_id:
        picked = [by_id[project_id]] if project_id in by_id else []
    elif group_id:
        picked = [p for p in all_projects
                  if (p.get("group_id") or "") == group_id and not p.get("archived")]
    else:
        picked = [p for p in all_projects if not p.get("archived")]
    pids = {p["id"] for p in picked}

    scenes: list[dict] = []
    for p in picked:
        for s in service.list_scenes(p["id"]):
            scenes.append({**s, "project_id": p["id"], "project_title": p["title"]})

    used: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}
    with db.txn() as conn:
        shot_rows = conn.execute(
            "SELECT project_id, shot_id, cast_json FROM shots").fetchall()
        cast_rows = conn.execute(
            "SELECT project_id, character_id FROM project_cast").fetchall()
    for r in shot_rows:
        if r["project_id"] not in pids:
            continue
        for cid in db.loads(r["cast_json"], []):
            used.setdefault(cid, []).append(r["shot_id"])
            seen.setdefault(cid, set()).add(r["project_id"])
    for r in cast_rows:
        if r["project_id"] in pids:
            seen.setdefault(r["character_id"], set()).add(r["project_id"])

    characters: list[dict] = []
    for c in service.list_characters(project_id or None):
        cid = c["character_id"]
        if (project_id or group_id) and cid not in seen:
            continue
        characters.append({
            **c,
            "used_in": used.get(cid, []),
            "projects": [{"id": p["id"], "title": p["title"]}
                         for p in picked if p["id"] in seen.get(cid, set())],
        })

    return {
        "characters": characters,
        "scenes": scenes,
        "outputs": _catalog_outputs(picked),
        "generated": _catalog_generated(pids, filtered=bool(project_id or group_id)),
        "project_id": project_id,
        "group_id": group_id,
        "projects": [{"id": p["id"], "title": p["title"]} for p in picked],
    }


def _catalog_outputs(projects: list[dict]) -> list[dict]:
    """把所选项目的成片汇总，带上片名，按时间倒序。"""
    out: list[dict] = []
    for p in projects:
        for o in list_outputs(p["id"]):
            out.append({**o, "project_title": p["title"]})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def _catalog_generated(pids: set[str], filtered: bool) -> list[dict]:
    """文生图产物（asset_candidates）。有筛选时只保留归属所选项目的。"""
    cands = list_candidates(limit=200)
    if not filtered:
        return cands
    return [c for c in cands if (c.get("project_id") or "") in pids]


# ---------------------------------------------------------------- 素材分组

def list_groups() -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute(
            "SELECT g.id, g.name, g.created_at, "
            "  (SELECT COUNT(*) FROM asset_candidates c WHERE c.group_id=g.id) AS count "
            "FROM asset_groups g ORDER BY g.created_at").fetchall()
    return [dict(r) for r in rows]


def ensure_group(name: str) -> dict:
    name = (name or "").strip()
    if not name:
        raise ValueError("分组名不能为空")
    gid = "ag_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    with db.txn() as conn:
        conn.execute("INSERT OR IGNORE INTO asset_groups(id, name, created_at) VALUES (?,?,?)",
                     (gid, name, db.now()))
        row = conn.execute("SELECT * FROM asset_groups WHERE id=?", (gid,)).fetchone()
    return dict(row)


ASSET_KINDS = ("character", "scene", "prop", "emotion")


def patch_candidate(cand_id: str, *, project_id: str | None = None,
                    group_id: str | None = None, kind: str | None = None) -> dict | None:
    """改候选图的归属（项目 / 分组 / 类型）；传 None 表示不改。"""
    if not get_candidate(cand_id):
        return None
    if kind is not None and kind not in ASSET_KINDS:
        raise ValueError(f"kind 必须是 {'/'.join(ASSET_KINDS)}")
    if project_id and not service.get_project(project_id):
        raise ValueError("项目不存在")
    if group_id:
        with db.txn() as conn:
            exists = conn.execute(
                "SELECT 1 FROM asset_groups WHERE id=?", (group_id,)).fetchone()
        if not exists:
            raise ValueError("分组不存在")
    sets, vals = [], []
    for col, val in (("project_id", project_id), ("group_id", group_id), ("kind", kind)):
        if val is not None:
            sets.append(f"{col}=?")
            vals.append(val)
    if sets:
        vals.append(cand_id)
        with db.txn() as conn:
            conn.execute(
                f"UPDATE asset_candidates SET {', '.join(sets)} WHERE id=?", vals)
    return get_candidate(cand_id)


def get_candidate(cand_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute(
            "SELECT * FROM asset_candidates WHERE id = ?", (cand_id,)
        ).fetchone()
    return _row_to_candidate(row) if row else None


def create_candidate(
    *,
    kind: str,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    path: str,
    negative: str = "",
    source_path: str = "",
    elapsed_s: float = 0.0,
    cand_id: str | None = None,
    project_id: str = "",
    group_id: str = "",
) -> dict:
    cand_id = cand_id or uuid.uuid4().hex[:12]
    with db.txn() as conn:
        conn.execute(
            """
            INSERT INTO asset_candidates (id, created_at, kind, prompt, negative,
                width, height, seed, steps, path, elapsed_s, source_path, adopted_to,
                project_id, group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                cand_id,
                db.now(),
                kind,
                prompt,
                negative,
                width,
                height,
                seed,
                steps,
                path,
                elapsed_s,
                source_path,
                project_id,
                group_id,
            ),
        )
    c = get_candidate(cand_id)
    if not c:
        raise RuntimeError("create_candidate: 写入后读回失败")
    return c


def delete_candidate(cand_id: str) -> bool:
    c = get_candidate(cand_id)
    if not c:
        return False
    # 只删候选图文件，不动已经入库到角色/场景目录的副本
    p = Path(c["path"]) if c["path"] else None
    with db.txn() as conn:
        conn.execute("DELETE FROM asset_candidates WHERE id = ?", (cand_id,))
    if p and p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    return True


@dataclass
class AdoptResult:
    cand_id: str
    target: str          # 人类可读，比如 "character 叶一恒" 或 "scene 堂屋"
    adopted_to: str      # 存进 candidates.adopted_to 字段的标识
    ok: bool
    error: str = ""


def adopt_candidate(
    cand_id: str,
    *,
    target_kind: str,        # "character" | "scene"
    character_id: str = "",
    project_id: str = "",
    scene_id: str = "",
    role: str = "main",       # 对 scene 生效："main"=替换母版, "alt"=加多角度
) -> AdoptResult:
    """把候选图入库到角色图集或场景母版。"""
    c = get_candidate(cand_id)
    if not c:
        return AdoptResult(cand_id, target="", adopted_to="", ok=False, error="候选图不存在")

    src = Path(c["path"]) if c["path"] else None
    if not src or not src.exists():
        return AdoptResult(cand_id, target="", adopted_to="", ok=False, error="候选图文件已丢失")

    stamp = time.strftime("%Y%m%d%H%M%S")
    suffix = src.suffix or ".png"

    try:
        if target_kind == "character":
            if not character_id:
                return AdoptResult(cand_id, "", "", ok=False, error="缺少 character_id")
            dest_dir = db.DATA_DIR / "characters"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f"{character_id}_{stamp}{suffix}"
            shutil.copy2(src, dest)

            # 追加到角色图集末尾（同 upload_refs 的逻辑）
            existing = {c["character_id"]: c for c in service.list_characters()}
            prev = existing.get(character_id, {"name": character_id, "description": "", "refs": [], "species": ""})
            refs = list(prev.get("refs", []))
            refs.append(str(dest))
            service.upsert_character(
                character_id,
                prev.get("name") or character_id,
                prev.get("description", ""),
                refs,
                force=True,
                species=prev.get("species", ""),
            )
            adopted_to = f"character:{character_id}"
            label = prev.get("name") or character_id

        elif target_kind == "scene":
            if not project_id or not scene_id:
                return AdoptResult(cand_id, "", "", ok=False, error="缺少 project_id 或 scene_id")
            dest_dir = db.DATA_DIR / "projects" / project_id / "scenes"
            dest_dir.mkdir(parents=True, exist_ok=True)
            if role == "main":
                dest = dest_dir / f"{scene_id}_{stamp}{suffix}"
                shutil.copy2(src, dest)
                scene = service.get_scene(project_id, scene_id) or {}
                service.upsert_scene(
                    project_id,
                    scene_id,
                    name=scene.get("name", ""),
                    camera=scene.get("camera", ""),
                    props=scene.get("props", ""),
                    light=scene.get("light", ""),
                    plate_path=str(dest),
                )
                adopted_to = f"scene:{project_id}:{scene_id}:main"
            else:  # alt —— 多角度
                dest = dest_dir / f"{scene_id}_alt_{stamp}{suffix}"
                shutil.copy2(src, dest)
                service.add_scene_plate(project_id, scene_id, str(dest))
                adopted_to = f"scene:{project_id}:{scene_id}:alt"
            scene = service.get_scene(project_id, scene_id) or {}
            label = scene.get("name") or scene_id

        else:
            return AdoptResult(cand_id, "", "", ok=False, error=f"未知 target_kind={target_kind}")

        with db.txn() as conn:
            conn.execute(
                "UPDATE asset_candidates SET adopted_to = ? WHERE id = ?",
                (adopted_to, cand_id),
            )
        return AdoptResult(cand_id, f"{target_kind} {label}", adopted_to, ok=True)

    except (OSError, shutil.SameFileError) as e:
        return AdoptResult(cand_id, "", "", ok=False, error=f"文件操作失败：{e}")
    except Exception as e:
        return AdoptResult(cand_id, "", "", ok=False, error=str(e))
