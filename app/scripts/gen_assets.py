#!/usr/bin/env python3
"""Generate missing assets (character refs, scene plates) for a script, then stop.

    python scripts/gen_assets.py drama/yuanxing_script.json [--force]

Character ref: one 5 s portrait render -> middle frame -> short edge >= 1024.
Scene plate : one 5 s empty-plate render -> middle frame -> plate_1.jpg
              (the shot prompts say "无人", so the plate stays people-free)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORTRAIT_SUFFIX = ("半身正面肖像，正面看向镜头，浅灰色纯色背景，柔和自然光，无遮挡，"
                   "高清皮肤与织物质感，35mm胶片。")
PLATE_SUFFIX = "空镜，画面中没有人，无人物。35mm胶片质感，实用光源，浅景深。"


def run(cmd: list[str], cwd: Path) -> int:
    return subprocess.run(cmd, cwd=str(cwd), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True).returncode


def render_frame(prompt: str, raw: Path, jpg: Path, scale_short: int) -> bool:
    raw.parent.mkdir(parents=True, exist_ok=True)
    if not raw.exists() or raw.stat().st_size < 100_000:
        rc = run([".venv/bin/open-video", prompt, "--aspect", "9:16",
                  "--duration", "5", "--output", str(raw)], REPO)
        if rc != 0 or not raw.exists():
            print(f"    render failed (rc={rc})")
            return False
    rc = run(["ffmpeg", "-y", "-v", "error", "-ss", "2.2", "-i", str(raw),
              "-frames:v", "1", "-update", "1", "-q:v", "3", str(jpg)], REPO)
    if rc != 0 or not jpg.exists():
        return False
    # ensure a usable resolution for character refs
    if scale_short:
        import struct
        with jpg.open("rb") as fh:
            head = fh.read(32)
        if head[:2] == b"\xff\xd8":
            # decode size via ffprobe to avoid a jpeg parser here
            out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                  "stream=width,height", "-of", "csv=p=0", str(jpg)],
                                 stdout=subprocess.PIPE, text=True).stdout.strip()
            try:
                w, h = [int(x) for x in out.split(",")[:2]]
            except ValueError:
                w = h = 0
            if w and min(w, h) < scale_short:
                tmp = jpg.with_suffix(".tmp.jpg")
                run(["ffmpeg", "-y", "-v", "error", "-i", str(jpg), "-vf",
                     f"scale=-2:{scale_short}", str(tmp)], REPO)
                if tmp.exists():
                    tmp.replace(jpg)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    spath = Path(args.script)
    if not spath.is_absolute():
        spath = REPO / spath
    script = json.loads(spath.read_text(encoding="utf-8"))

    tmp = REPO / "segments" / "_assets"
    ok_all = True

    print("== 角色参考图 ==")
    for c in script.get("characters") or []:
        ref = Path(c["reference_image"])
        if not ref.is_absolute():
            ref = REPO / ref
        ref = ref.with_suffix(".png")
        if ref.exists() and not args.force:
            print(f"  {c['name']}: exists")
            continue
        ref.parent.mkdir(parents=True, exist_ok=True)
        prompt = f"{c['description']}。{PORTRAIT_SUFFIX}"
        good = render_frame(prompt, tmp / f"char_{ref.parent.name}.mp4", ref, 1024)
        print(f"  {c['name']}: {'ok' if good else 'FAILED'}")
        ok_all &= good

    print("== 场景母版图 ==")
    for s in script.get("scenes") or []:
        sid = s["scene_id"]
        lib = REPO / "drama" / "library" / "scenes" / sid
        plate = lib / "plate_1.jpg"
        if plate.exists() and not args.force:
            print(f"  {sid}: exists")
            continue
        lib.mkdir(parents=True, exist_ok=True)
        prompt = f"{s.get('location', '')}。{PLATE_SUFFIX}"
        good = render_frame(prompt, tmp / f"scene_{sid}.mp4", plate, 0)
        card = lib / "card.json"
        data = json.loads(card.read_text(encoding="utf-8")) if card.is_file() else {}
        data.update({
            "scene_id": sid,
            "name": data.get("name") or sid,
            "camera": s.get("camera") or data.get("camera") or "",
            "props": s.get("props") or data.get("props") or "",
            "light": s.get("light") or data.get("light") or "",
            "images": [str(plate.relative_to(REPO))] if plate.exists() else [],
        })
        card.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {sid}: {'ok' if good else 'FAILED'}")
        ok_all &= good

    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
