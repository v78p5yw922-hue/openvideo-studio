"""Domain service: projects, shots, takes, reviews, plans.

Every write goes through here so fingerprints and cascades cannot be forgotten
at a call site.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from . import db
from .model import (RENDER_PARAMS_DEFAULT, cascade, next_version, sha256_file,
                    shot_fingerprint, take_name)

# render budget measured on this box: 5 s @768x1344, 20 steps
MIN_PER_SHOT_FULL = 8.5
MIN_PER_SHOT_DRAFT = 5.0
DIALOGUE_MAX_CHARS = 24      # 5 s of Mandarin at a natural pace

EXEC_ALLOWED = False          # flipped by the confirmed-run endpoint only


# ---------------------------------------------------------------- projects

def create_project(project_id: str, title: str, style: str = "") -> dict:
    now = db.now()
    with db.txn() as conn:
        conn.execute(
            "INSERT INTO projects(id,title,style,created_at,updated_at) VALUES (?,?,?,?,?)",
            (project_id, title, style, now, now))
        db.emit(conn, project_id, "", "project.created", {"title": title})
    return get_project(project_id)


def get_project(project_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return db.row_to_dict(row)


def list_projects() -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ assets

def upsert_character(cid: str, name: str, description: str, refs: list[str],
                     force: bool = False) -> dict:
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
            """INSERT INTO characters(character_id,name,description,refs_json,primary_ref,updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(character_id) DO UPDATE SET
                 name=excluded.name, description=excluded.description,
                 refs_json=excluded.refs_json, primary_ref=excluded.primary_ref,
                 updated_at=excluded.updated_at""",
            (cid, name, description, json.dumps(refs, ensure_ascii=False), primary, db.now()))
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
    return get_scene(project_id, scene_id)


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


def get_scene(project_id: str, scene_id: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM scenes WHERE project_id=? AND scene_id=?",
                           (project_id, scene_id)).fetchone()
    return db.row_to_dict(row)


def list_scenes(project_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM scenes WHERE project_id=? ORDER BY scene_id",
                            (project_id,)).fetchall()
    return [dict(r) for r in rows]


def list_characters() -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM characters ORDER BY name").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["refs"] = db.loads(d.pop("refs_json"), [])
        out.append(d)
    return out


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
               "sound", "transition", "anchor", "duration", "first_frame")


def _shot_row_to_dict(row) -> dict:
    d = dict(row)
    d["cast"] = db.loads(d.pop("cast_json"), [])
    return d


def list_shots(project_id: str) -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM shots WHERE project_id=? ORDER BY ordinal",
                            (project_id,)).fetchall()
    return [_shot_row_to_dict(r) for r in rows]


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
            conn.execute(
                """INSERT INTO shots(project_id,shot_id,ordinal,scene_id,cast_json,camera,prompt,
                                     dialogue,sound,transition,anchor,duration,first_frame,fingerprint,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(project_id,shot_id) DO UPDATE SET
                     ordinal=excluded.ordinal, scene_id=excluded.scene_id, cast_json=excluded.cast_json,
                     camera=excluded.camera, prompt=excluded.prompt, dialogue=excluded.dialogue,
                     sound=excluded.sound, transition=excluded.transition, anchor=excluded.anchor,
                     duration=excluded.duration, first_frame=excluded.first_frame,
                     updated_at=excluded.updated_at""",
                (project_id, sid, i,
                 pick("scene_id", ""),
                 json.dumps(s.get("cast") if s.get("cast") is not None
                            else db.loads(prev.get("cast_json", "[]"), []), ensure_ascii=False),
                 pick("camera", ""), pick("prompt", ""),
                 pick("dialogue", ""), pick("sound", ""),
                 pick("transition", "cut"), pick("anchor", ""),
                 float(pick("duration", 5.0) or 5.0), pick("first_frame", ""), "", now))
        for sid in existing - keep:
            conn.execute("DELETE FROM shots WHERE project_id=? AND shot_id=?", (project_id, sid))
        conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
        db.emit(conn, project_id, "", "shots.saved", {"count": len(shots)})
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
        fp = shot_fingerprint(sh, scenes.get(sh["scene_id"]), refs, params, prev_fp)
        if fp != sh["fingerprint"]:
            with db.txn() as conn:
                conn.execute("UPDATE shots SET fingerprint=? WHERE project_id=? AND shot_id=?",
                             (fp, project_id, sh["shot_id"]))
        prev_fp = fp
        sh["fingerprint"] = fp
        updated.append(sh)
    return updated


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
                version: int | None = None) -> dict:
    with db.txn() as conn:
        versions = [r["version"] for r in conn.execute(
            "SELECT version FROM takes WHERE project_id=? AND shot_id=?",
            (project_id, shot_id)).fetchall()]
        v = version if version else next_version(versions)
        conn.execute(
            """INSERT INTO takes(project_id,shot_id,version,path,fingerprint,elapsed_s,peak_mib,created_at,note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (project_id, shot_id, v, path, fingerprint, elapsed_s, peak_mib, db.now(), note))
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


def adopt_take(project_id: str, shot_id: str) -> dict:
    """Declare the latest take valid for the shot's *current* inputs.

    Needed after a pipeline change (or when a field was touched by accident) and
    the clip is known to be right; re-rendering would cost minutes.
    """
    shots = recompute_fingerprints(project_id)
    sh = next((s for s in shots if s["shot_id"] == shot_id), None)
    if sh is None:
        raise ValueError(f"镜头不存在: {shot_id}")
    take = latest_take(project_id, shot_id)
    if not take:
        raise ValueError("这一镜还没有片段")
    with db.txn() as conn:
        conn.execute("UPDATE takes SET fingerprint=? WHERE project_id=? AND shot_id=? AND version=?",
                     (sh["fingerprint"], project_id, shot_id, take["version"]))
        db.emit(conn, project_id, shot_id, "take.adopted",
                {"version": take["version"]})
    return {"shot_id": shot_id, "version": take["version"],
            "state": shot_state(project_id, sh)["state"]}


def assemble(project_id: str, width: int = 1344, height: int = 768,
             with_subs: bool = True) -> dict:
    """Stitch every shot's latest take into one film (no GPU model involved)."""
    import subprocess
    shots = recompute_fingerprints(project_id)
    if not shots:
        raise ValueError("没有镜头")
    missing = [s["shot_id"] for s in shots if not latest_take(project_id, s["shot_id"])]
    if missing:
        raise ValueError(f"这些镜头还没有片段: {', '.join(missing)}")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = db.DATA_DIR / "projects" / project_id
    staging = root / "assemble" / stamp
    staging.mkdir(parents=True, exist_ok=True)
    for i, sh in enumerate(shots, start=1):
        take = latest_take(project_id, sh["shot_id"])
        link = staging / f"{i:04d}.mp4"
        if link.exists():
            link.unlink()
        link.symlink_to(take["path"])

    out_dir = root / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    merged = out_dir / f"{project_id}_{stamp}.mp4"
    merged_raw = out_dir / f"{project_id}_{stamp}_merge.mp4"
    cp = subprocess.run([str(VENV_PY), "scripts/merge.py", "--segments", str(staging),
                         "--out", str(merged_raw),
                         "--width", str(width), "--height", str(height)],
                        cwd=str(REPO_DIR), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0 or not merged_raw.is_file():
        raise RuntimeError((cp.stdout or "")[-400:])

    final = merged_raw
    dialogues = [s for s in shots if (s.get("dialogue") or "").strip()]
    if with_subs and dialogues:
        from ..exec import render_adapter
        script_path = render_adapter.write_script(project_id, width, height)
        cp2 = subprocess.run([str(VENV_PY), "scripts/finalize.py", str(script_path),
                              "--in", str(merged_raw), "--out", str(merged),
                              "--segments", str(staging),
                              "--width", str(width), "--height", str(height)],
                             cwd=str(REPO_DIR), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True)
        if cp2.returncode == 0 and merged.is_file():
            final = merged

    return {"out": str(final), "url": f"/outputs/{project_id}/{final.name}",
            "shots": len(shots), "subtitles": bool(dialogues) and with_subs}


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
                 cast_remove: list[str] | None = None) -> dict:
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
            "added": added, "removed": removed, "warnings": parsed["warnings"]}


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
            if not cur.get("description") and c.get("description"):
                upsert_character(cur["character_id"], name, c["description"], cur.get("refs", []))
        else:
            upsert_character(c["character_id"], name, c.get("description", ""), c.get("refs", []))
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
    if not get_project(project_id):
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


def plan(project_id: str, shot_ids: list[str] | None = None, draft: bool = False) -> dict:
    all_shots = recompute_fingerprints(project_id)
    shots = all_shots
    if shot_ids:
        want = set(shot_ids)
        shots = [s for s in all_shots if s["shot_id"] in want]
    order_index = {s["shot_id"]: i for i, s in enumerate(all_shots)}
    scenes = {s["scene_id"]: s for s in list_scenes(project_id)}

    warnings: list[dict] = []
    todo: list[dict] = []
    for sh in shots:
        st = shot_state(project_id, sh)
        take = st.get("take") or {}
        is_final_take = bool(take) and (take.get("note") or "") != "draft"
        # a draft take must not block the final render; a final take does
        if st["state"] == "passed":
            continue
        if st["state"] == "review" and is_final_take and not draft:
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
                           "draft": draft, "created": time.time()}
    for old in [k for k, v in PLAN_STORE.items()
                if time.time() - v["created"] > PLAN_TTL_S]:
        PLAN_STORE.pop(old, None)
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
VENV_PY = REPO_DIR / ".venv/bin/python"


def fail_orphan_running(reason: str = "服务重启，任务中断") -> int:
    """A job left in 'running' by a dead process would block the queue forever."""
    with db.txn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET state='failed', error=?, finished_at=? WHERE state='running'",
            (reason, db.now()))
        return cur.rowcount


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
