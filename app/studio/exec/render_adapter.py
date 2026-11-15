"""Build the renderer's script JSON from the database.

The database is the source of truth; this module is the only place that knows
the legacy `render_drama.py` input format.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..core import db, service


def build_script(project_id: str, width: int = 1344, height: int = 768) -> dict:
    project = service.get_project(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")
    shots = service.list_shots(project_id)
    scenes = service.list_scenes(project_id)
    chars = {c["character_id"]: c for c in service.list_characters()}

    out_scenes = []
    for sc in scenes:
        out_scenes.append({
            "scene_id": sc["scene_id"],
            "location": sc.get("name") or sc["scene_id"],
            "time_of_day": "",
            "atmosphere": sc.get("light", ""),
            "reference_image": None,
            "images": [sc["plate_path"]] if sc.get("plate_path") else [],
            "camera": sc.get("camera", ""),
            "props": sc.get("props", ""),
            "light": sc.get("light", ""),
        })

    used = []
    for sh in shots:
        for cid in sh["cast"]:
            c = chars.get(cid)
            if c and c not in used:
                used.append(c)
    out_chars = [{
        "character_id": c["character_id"],
        "name": c["name"],
        "description": c.get("description", ""),
        "reference_image": c.get("primary_ref", ""),
        "reference_images": c.get("refs", []),
    } for c in used]

    out_shots = [{
        "shot_id": sh["shot_id"],
        "scene_id": sh["scene_id"],
        "duration_sec": sh["duration"],
        "characters_present": [chars[c]["name"] for c in sh["cast"] if c in chars],
        "camera": sh["camera"],
        "visual_prompt": sh["prompt"],
        "dialogue": sh["dialogue"],
        "soundscape": sh.get("sound", ""),
        "transition": sh["transition"],
        "anchor": sh.get("anchor", ""),
    } for sh in shots]

    return {
        "project_id": project_id,
        "title": project["title"],
        "target_duration": round(sum(s["duration_sec"] for s in out_shots), 1),
        "visual_style": project.get("style", ""),
        "characters": out_chars,
        "scenes": out_scenes,
        "shots": out_shots,
    }


def write_script(project_id: str, width: int = 1344, height: int = 768) -> Path:
    dest = db.DATA_DIR / "projects" / project_id / "render.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = build_script(project_id, width, height)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
