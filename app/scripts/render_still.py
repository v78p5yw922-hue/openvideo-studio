#!/usr/bin/env python3
"""Render a single still from a text prompt (character / scene / prop plates).

Reuses render_drama's workflow builder so stills and shots share the same
pipeline, then extracts a frame from the generated clip.

    python scripts/render_still.py --prompt "..." --out x.png \
        --aspect 9:16 --duration 3 --seed 42 --server http://127.0.0.1:8188
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_render_drama():
    spec = importlib.util.spec_from_file_location("rd", REPO / "scripts/render_drama.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1344)
    ap.add_argument("--duration", type=float, default=3.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--at", type=float, default=0.55, help="which frame to keep (0-1)")
    ap.add_argument("--first-frame", default=None,
                    help="use this image as frame 1 (i2v) — keeps the character inside a real set")
    args = ap.parse_args()

    rd = load_render_drama()
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    shot = {"shot_id": "still", "visual_prompt": args.prompt,
            "characters_present": [], "duration_sec": args.duration}
    lock = None
    if args.first_frame:
        lock = Path(args.first_frame).expanduser().resolve()
        if not lock.is_file():
            print(f"first-frame not found: {lock}", file=sys.stderr)
            return 2
    wf = rd.build_workflow(shot, {}, "", None, args.width, args.height,
                           args.steps, args.seed, lock_frame=lock)
    wf["save_video"]["inputs"]["filename_prefix"] = f"still_{int(time.time())}"

    try:
        r = rd.post_json(f"{args.server}/prompt",
                         {"prompt": wf, "client_id": "open-video-still"})
    except Exception as e:  # noqa: BLE001
        print(f"submit failed: {e}", file=sys.stderr)
        return 2
    if r.get("node_errors"):
        print(f"rejected: {str(r['node_errors'])[:400]}", file=sys.stderr)
        return 3
    pid = r["prompt_id"]

    t0 = time.time()
    info = None
    while time.time() - t0 < 900:
        try:
            h = rd.get_json(f"{args.server}/history/{pid}")
        except Exception:  # noqa: BLE001
            h = {}
        if pid in h:
            info = json.dumps({"outputs": h[pid].get("outputs", {})})
            break
        time.sleep(3)
    if not info:
        print("timeout", file=sys.stderr)
        return 4

    tmp = out.with_suffix(".mp4")
    if not rd.download_output(args.server, info, tmp):
        print("download failed", file=sys.stderr)
        return 5

    dur = rd.ffprobe_duration(tmp)
    ts = max(0.1, dur * args.at)
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{ts:.2f}", "-i", str(tmp),
                         "-frames:v", "1", "-update", "1", "-q:v", "2", str(out)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tmp.unlink(missing_ok=True)
    if cp.returncode != 0 or not out.exists():
        print(f"frame extract failed: {(cp.stdout or '')[-300:]}", file=sys.stderr)
        return 6
    print(json.dumps({"ok": True, "out": str(out), "elapsed_s": round(time.time() - t0, 1)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
