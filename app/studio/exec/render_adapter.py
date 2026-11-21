"""Build the renderer's script JSON from the database.

The database is the source of truth; this module is the only place that knows
the legacy `render_drama.py` input format.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from ..core import db, service

REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import prompt_compiler as pc  # noqa: E402


def build_script(project_id: str, width: int = 1344, height: int = 768) -> dict:
    project = service.get_project(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")
    shots = service.list_shots(project_id)
    scenes = service.list_scenes(project_id)
    chars = {c["character_id"]: c for c in service.list_characters(project_id)}

    out_scenes = []
    for sc in scenes:
        out_scenes.append({
            "scene_id": sc["scene_id"],
            "location": sc.get("name") or sc["scene_id"],
            "time_of_day": "",
            "atmosphere": sc.get("light", ""),
            "reference_image": None,
            "images": ([sc["plate_path"]] if sc.get("plate_path") else []) + list(sc.get("extra_plates") or []),
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
        "music": sh.get("music", ""),
        "music_mode": (((project.get("audio") or {}).get("bgm") or {}).get("mode", "")
                       if isinstance(project.get("audio"), dict) else ""),
        "transition": sh["transition"],
        "anchor": sh.get("anchor", ""),
        "first_frame": sh.get("first_frame", ""),
        "last_frame": sh.get("last_frame", ""),
        "chain_ref": bool(sh.get("chain_ref")),
        # 槽位计划：用哪几张图、按什么顺序（9 槽上限的唯一真相）
        "ref_plan": sh.get("ref_plan") or {},
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


def _renderer_file(value: str | Path | None) -> Path | None:
    """按渲染器的工作目录解析路径，只检查存在性，不创建或读取内容。"""
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (REPO / path).resolve()
    return path if path.is_file() else None


def _preview_lock(script: dict, index: int) -> Path | None:
    """复现单镜渲染的首帧候选优先级，全程只读，不抽取 chain 末帧。"""
    shots = script["shots"]
    shot = shots[index]
    pinned = _renderer_file(shot.get("first_frame"))
    if pinned is not None:
        return pinned

    lock = None
    if str(shot.get("transition")) == "chain" and index > 0:
        previous = shots[index - 1]
        same_cast = (set(shot.get("characters_present") or [])
                     == set(previous.get("characters_present") or []))
        if same_cast:
            take = service.latest_take(script["project_id"], previous["shot_id"])
            if take:
                # 视频路径仅作为“上游可抽帧”的标记；实际渲染会在这里生成末帧。
                lock = _renderer_file(take.get("path"))

    scene = next((s for s in script["scenes"] if s["scene_id"] == shot.get("scene_id")), None)
    if lock is None and scene and not (shot.get("characters_present") or []):
        # 执行器以 --only 渲染单镜，render_drama 的 seen_scenes 到本镜时仍为空。
        first_in_scene = True
        want_anchor = (str(shot.get("anchor") or "") == "plate"
                       or (first_in_scene and str(shot.get("transition")) != "chain"))
        images = scene.get("images") or ([scene["reference_image"]]
                                          if scene.get("reference_image") else [])
        if want_anchor and images:
            lock = _renderer_file(images[0])
    return lock


def compile_preview(project_id: str, shot_id: str, prompt: str) -> dict:
    """编译未保存草稿；不写数据库、render.json、任务或任何渲染产物。"""
    started = time.perf_counter()
    script = build_script(project_id)
    index = next((i for i, s in enumerate(script["shots"]) if s["shot_id"] == shot_id), None)
    if index is None:
        raise ValueError(f"镜头不存在: {shot_id}")

    shot = dict(script["shots"][index])
    shot["visual_prompt"] = prompt
    script["shots"][index] = shot
    scene = next((s for s in script["scenes"] if s["scene_id"] == shot.get("scene_id")), None)

    chars: dict[str, dict] = {}
    for char in script["characters"]:
        chars[str(char.get("character_id") or char.get("name"))] = char
        chars[str(char.get("name"))] = char
    pairs = [(name, chars.get(name) or {}) for name in (shot.get("characters_present") or [])]
    names = [char.get("name") or name for name, char in pairs]
    descriptions = {char.get("name") or name: (char.get("description") or "")
                    for name, char in pairs}

    lock = _preview_lock(script, index)
    instruction = pc.i2v_instruction() if pc.has_effective_lock(shot, lock) else ""
    report = pc.compile_shot_report(
        shot, script.get("visual_style") or "", names, scene,
        instruction=instruction, cast_descriptions=descriptions,
    )
    report["elapsed_ms"] = round(max(0.0, time.perf_counter() - started) * 1000, 3)
    return report


def write_script(project_id: str, width: int = 1344, height: int = 768) -> Path:
    dest = db.DATA_DIR / "projects" / project_id / "render.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = build_script(project_id, width, height)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
