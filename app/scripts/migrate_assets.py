#!/usr/bin/env python3
"""Move assets into a browsable library and derive 3 scene plates.

Layout after this runs:

    drama/library/
        characters/<char_id>/
            ref_1.png
            card.json        name / description / key_features / updated_at
        scenes/<scene_id>/
            plate_1.jpg      场景母版图（从已有片段抽帧，不重新渲染）
            card.json        name / description / camera / props / light

Backwards compatibility: `drama/cast/<id>` is replaced by a symlink to the new
location, so existing scripts that point at drama/cast/... keep working.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

REPO = Path.home() / "openvideo/video-agent"
DATA = REPO / "drama"
LIB = DATA / "library"
CHARS = LIB / "characters"
SCENES = LIB / "scenes"
OLD_CAST = DATA / "cast"


def migrate_characters() -> None:
    CHARS.mkdir(parents=True, exist_ok=True)
    db = DATA / "cast.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS characters (
        character_id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
        images TEXT NOT NULL, voice TEXT, updated_at TEXT NOT NULL)""")
    rows = conn.execute("SELECT * FROM characters").fetchall()
    moved = 0
    for r in rows:
        cid = r["character_id"]
        src = OLD_CAST / cid
        dst = CHARS / cid
        if src.is_dir() and not dst.exists():
            dst.mkdir(parents=True, exist_ok=True)
            for f in src.iterdir():
                if f.is_file():
                    shutil.move(str(f), str(dst / f.name))
            try:
                src.rmdir()
            except OSError:
                pass
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "card.json").write_text(json.dumps({
            "character_id": cid, "name": r["name"], "description": r["description"],
            "images": [str(p.relative_to(REPO)) for p in sorted(dst.glob("*.png"))],
            "updated_at": r["updated_at"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        imgs = json.dumps([str(p.relative_to(REPO)) for p in sorted(dst.glob("*.png"))])
        conn.execute("UPDATE characters SET images=? WHERE character_id=?", (imgs, cid))
        moved += 1
    conn.commit()
    print(f"  characters migrated: {moved}")

    # keep old paths working
    if OLD_CAST.exists() and not OLD_CAST.is_symlink():
        shutil.rmtree(OLD_CAST, ignore_errors=True)
    if not OLD_CAST.exists():
        OLD_CAST.symlink_to(CHARS, target_is_directory=True)
        print("  drama/cast -> library/characters (symlink)")


def rewrite_script_paths() -> None:
    changed = 0
    for p in list((DATA).glob("*.json")) + list((DATA / "scripts").glob("*.json")):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        dirty = False
        for c in s.get("characters") or []:
            ref = str(c.get("reference_image") or "")
            if ref.startswith("drama/cast/"):
                c["reference_image"] = ref.replace("drama/cast/", "drama/library/characters/")
                dirty = True
        if dirty:
            p.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
            changed += 1
    print(f"  scripts rewritten: {changed}")


PLATES = [
    ("scene_hutong_shuxia", "树下", "s02", "0002.mp4",
     "北京老胡同中段，大槐树下；机位平视略仰，位于树下偏西朝北",
     "大槐树（画面左1/3）、藤椅（中）、小马扎（右）、搪瓷缸（藤椅扶手）、西瓜（小桌）",
     "午后四点半，暖白偏金光，树荫光斑，地面高光"),
    ("scene_hutong_menkou", "院门口", "s08", "0008.mp4",
     "胡同中段，红漆院门前；机位平视，朝院门方向",
     "红漆院门（中偏右）、门槛、门环、晾衣绳白衬衫（上）、蜂窝煤（左）",
     "门外亮白日光，门内暗，明暗交界在门槛"),
    ("scene_hutong_wunei", "屋内", "s12", "0012.mp4",
     "老北京堂屋；机位在门内朝里，平视略低",
     "窗帘半拉（左）、电扇（中偏后）、木桌西瓜（中）、纱窗（右）、门框（左前）",
     "室内暗调，门缝一道白光切在地面，电扇摇头光影"),
]


def derive_scene_plates() -> None:
    SCENES.mkdir(parents=True, exist_ok=True)
    seg = REPO / "segments/hutong_xiaoli"
    for sid, name, shot, fname, cam, props, light in PLATES:
        d = SCENES / sid
        d.mkdir(parents=True, exist_ok=True)
        src = seg / fname
        dst = d / "plate_1.jpg"
        if src.is_file():
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.2", "-i", str(src),
                            "-frames:v", "1", "-update", "1", "-q:v", "3", str(dst)],
                           check=False)
        (d / "card.json").write_text(json.dumps({
            "scene_id": sid, "name": name, "source_shot": shot,
            "camera": cam, "props": props, "light": light,
            "images": [str(dst.relative_to(REPO))] if dst.exists() else [],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  scene {sid}: {'plate ok' if dst.exists() else 'no plate'}")


def main() -> None:
    print("== 迁移角色资产 ==")
    migrate_characters()
    print("== 更新剧本里的引用路径 ==")
    rewrite_script_paths()
    print("== 生成场景母版（从已有片段抽帧，不重渲） ==")
    derive_scene_plates()
    print("\n== 结构 ==")
    for p in sorted(LIB.rglob("card.json"))[:12]:
        print(f"  {p.relative_to(DATA)}")


if __name__ == "__main__":
    main()
