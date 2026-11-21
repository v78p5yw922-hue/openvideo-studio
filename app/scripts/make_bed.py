#!/usr/bin/env python3
"""预生成整集音频床（BGM / 环境声）——用本机 H3 生成一条"只有声音"的片段，抽出音轨。

为什么要单独做：
  模型逐镜生成 BGM 会让每个切点都"重头开始"。所以改为——一次生成一条长床，
  合成时循环铺满整集。默认走这条（mode=auto），也可以在合并页换成自定义文件。

用法：
    python scripts/make_bed.py --kind bgm --seconds 10 --out studio-data/beds/x_bgm.m4a
    python scripts/make_bed.py --kind ambience --seconds 10 --out studio-data/beds/x_amb.m4a
    ... --dry-run     只打印工作流，不渲染
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

ENGINE = "http://127.0.0.1:8288"

PROMPTS = {
    "bgm": ("integrated_multimodal_description: [Shot 1] an empty frame, no people, no objects, "
            "only soft light。画面里没有任何主体。纯器乐配乐：木吉他与竹笛，速度缓慢，"
            "旋律平缓循环，适合 1996 年北方农村清晨的日常。"
            "\n\noverall_soundscape: 极轻的房间底噪"
            "\n\nnon_diegetic_music: 木吉他与竹笛，速度缓慢，旋律平缓循环"),
    "ambience": ("integrated_multimodal_description: [Shot 1] an empty frame, no people。"
                 "画面里没有任何主体。环境声：夏日清晨的院子底噪，远处知了、偶尔的鸡叫、"
                 "轻微的风声。"
                 "\n\noverall_soundscape: 夏日清晨院子底噪，知了连成一片，偶有鸡叫与风声"
                 "\n\nnon_diegetic_music: none"),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="预生成整集音频床")
    ap.add_argument("--kind", choices=["bgm", "ambience"], default="bgm")
    ap.add_argument("--seconds", type=int, default=10, choices=[5, 8, 10],
                    help="生成时长档（H3 只有 5/8/10）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine", default=ENGINE)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=288)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    import render_drama as rd  # noqa: PLC0415

    script = {
        "project_id": "bed",
        "title": f"bed-{args.kind}",
        "visual_style": "minimal, no people",
        "characters": [],
        "scenes": [],
        "shots": [{
            "shot_id": "bed", "scene_id": "", "duration_sec": float(args.seconds),
            "characters_present": [], "camera": "", "visual_prompt": PROMPTS[args.kind],
            "dialogue": "", "soundscape": "", "music": "", "transition": "cut",
            "anchor": "", "first_frame": "", "chain_ref": False, "ref_plan": {},
        }],
    }
    work = Path(tempfile.mkdtemp(prefix="bed_"))
    sp = work / "script.json"
    sp.write_text(json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")
    wf = rd.build_workflow(script["shots"][0], {}, "", None, args.width, args.height,
                           steps=20, seed=args.seed, lock_frame=None)
    if args.dry_run:
        print(json.dumps({"nodes": sorted(wf), "length": wf["h3_i2v"]["inputs"]["length"]},
                         ensure_ascii=False))
        return 0

    out_dir = work / "out"
    cp = subprocess.run([sys.executable, str(REPO / "scripts/render_drama.py"), str(sp),
                         "--only", "bed", "--out", str(out_dir), "--no-upscale",
                         "--width", str(args.width), "--height", str(args.height),
                         "--port", "8288", "--steps", "20", "--seed", str(args.seed)],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        print("渲染失败：", (cp.stdout or cp.stderr or "")[-400:], file=sys.stderr)
        return 1
    clips = sorted(out_dir.glob("*.mp4"))
    if not clips:
        print("没有产出", file=sys.stderr)
        return 1

    dst = Path(args.out).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clips[-1]),
                         "-vn", "-c:a", "aac", "-b:a", "192k", str(dst)],
                        capture_output=True, text=True)
    if cp.returncode != 0 or not dst.is_file():
        print("抽音轨失败：", (cp.stderr or "")[-300:], file=sys.stderr)
        return 1
    print(f"OK {dst}  {dst.stat().st_size // 1024} KB")
    shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
