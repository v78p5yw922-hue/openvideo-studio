#!/usr/bin/env python3
"""Upscale/re-draw finished H3 segments with ComfyUI-H3-Video-Upsampler.

Why this exists: H3 spreads one pass across composition, motion and audio, so
texture (wheat ears, skin) comes out soft.  The fix is two-stage — H3 lays down
the clip, then a dedicated latent-upscale + guided re-draw pass restores detail.

The upsampler keeps the clip's own mouths/motion/**soundtrack** (it re-uses the
source encode), which is why it is preferred over a generic video super-res:
our audio is already sync-aligned by merge.py and must not be re-timed.

Usage
-----
    python scripts/upscale_segments.py --segments segments/wheat_1990 \
        --out segments/wheat_1990_2k --megapixels 2.0 --limit 1
    python scripts/upscale_segments.py ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
COMFY = REPO / "ComfyUI"          # overridden by --comfy-dir
COMFY_INPUT = COMFY / "input"
COMFY_OUTPUT = COMFY / "output"
DEFAULT_SERVER = "http://127.0.0.1:8188"

UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
VAE_VIDEO = "minimax_h3_video_vae_int8_convrot.safetensors"
VAE_AUDIO = "minimax_h3_audio_vae_fp32.safetensors"
UPSCALE_MODEL = "minimax_h3_latent_upscaler_2d_conv_v1_bf16.safetensors"
LORA = "open_h3_regenerate_2k_r256pp_bf16.safetensors"


def log(msg: str) -> None:
    print(f"[upscale {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def post_json(url: str, payload: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def build_workflow(video_name: str, megapixels: float, method: str,
                   sharpness: int, prefix: str) -> dict:
    return {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_VIDEO}},
        "audio_vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE_AUDIO}},
        "load": {"class_type": "LoadVideo", "inputs": {"file": video_name}},
        "up": {"class_type": "H3VideoUpsamplerToFile", "inputs": {
            "video": ["load", 0],
            "model": ["unet", 0],
            "vae": ["vae", 0],
            "audio_vae": ["audio_vae", 0],
            "upscale_model": UPSCALE_MODEL,
            "megapixels": float(megapixels),
            "denoise": 0.8,
            "lora": LORA,
            "tile_tokens": 0,
            "sampler_name": "euler",
            "redraw_seed": 42,
            "attention": "sparse",
            "chunk_join": "stepped",
            "chunking": True,
            "tiling": True,
            "redraw_start": 0.6,
            "redraw_steps": "3 (recipe)",
            "frame_gen": "off",
            "codec": "h264",
            "quality": "max",
            "filename_prefix": prefix,
            "frame_rate": "source",
            "reuse_encoded": True,
            "method": method,
            "sharpness": int(sharpness),
        }},
    }


def run_job(server: str, wf: dict, timeout: float = 1800) -> tuple[bool, str]:
    try:
        r = post_json(f"{server}/prompt", {"prompt": wf, "client_id": "open-video-upscale"})
    except Exception as e:  # noqa: BLE001
        return False, f"submit failed: {e}"
    if r.get("node_errors"):
        return False, f"rejected: {str(r['node_errors'])[:400]}"
    pid = r["prompt_id"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            h = get_json(f"{server}/history/{pid}")
        except Exception:  # noqa: BLE001
            h = {}
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") != "success":
                return False, f"status={st.get('status_str')}"
            outs = h[pid].get("outputs", {})
            # prefer files written by the upscaler node, then any .mp4 output
            best = None
            for node_id, payload in outs.items():
                for items in payload.values():
                    if not isinstance(items, list):
                        continue
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        fn = item.get("filename") or ""
                        if not fn.lower().endswith(".mp4"):
                            continue
                        rel = f"{item.get('subfolder') or ''}/{fn}".lstrip("/")
                        if "up" in node_id or "up" in fn:
                            return True, rel
                        best = best or rel
            if best:
                return True, best
            return True, json.dumps(outs)[:300]
        time.sleep(3)
    return False, "timeout"


def sharpness(path: Path) -> float | None:
    """Variance of Laplacian on a native-resolution frame (higher = crisper)."""
    cp = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1.0", "-i", str(path),
                         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                        stdout=subprocess.PIPE)
    raw = cp.stdout
    if not raw:
        return None
    cp2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], stdout=subprocess.PIPE, text=True)
    try:
        w, h = [int(x) for x in cp2.stdout.strip().splitlines()[0].split(",")[:2]]
    except (ValueError, IndexError):
        return None
    if w * h * 1 != len(raw):
        return None
    img = np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(np.float32)
    lap = (4 * img[1:-1, 1:-1] - img[:-2, 1:-1] - img[2:, 1:-1]
           - img[1:-1, :-2] - img[1:-1, 2:])
    return float(lap.var())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Upscale finished H3 segments")
    ap.add_argument("--segments", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--comfy-dir", default=str(COMFY),
                    help="ComfyUI tree serving --server (its input/ and output/ are used)")
    ap.add_argument("--megapixels", type=float, default=2.0)
    ap.add_argument("--method", choices=["regen2k", "ic"], default="regen2k")
    ap.add_argument("--sharpness", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    global COMFY_INPUT, COMFY_OUTPUT
    _comfy = Path(args.comfy_dir).expanduser().resolve()
    COMFY_INPUT = _comfy / "input"
    COMFY_OUTPUT = _comfy / "output"

    seg_dir = Path(args.segments).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    segs = sorted(p for p in seg_dir.glob("*.mp4") if re.match(r"^\d{4}\.mp4$", p.name))
    if args.limit:
        segs = segs[: args.limit]
    if not segs:
        print("no segments", file=sys.stderr)
        return 2

    rows = []
    for p in segs:
        dst = out_dir / p.name
        if dst.exists() and dst.stat().st_size > 100 * 1024:
            log(f"skip {p.name} (exists)")
            continue
        staged = COMFY_INPUT / f"ov_up_{p.stem}.mp4"
        COMFY_INPUT.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, staged)
        wf = build_workflow(staged.name, args.megapixels, args.method,
                            args.sharpness, f"video/ov_up_{p.stem}")
        if args.dry_run:
            log(f"would upscale {p.name} → {dst}")
            continue
        before = sharpness(p)
        t0 = time.time()
        ok, info = run_job(args.server, wf, args.timeout)
        elapsed = time.time() - t0
        if not ok:
            log(f"FAIL {p.name}: {info[:300]}")
            return 1
        src = (COMFY_OUTPUT / info).resolve() if not Path(info).is_absolute() else Path(info)
        if not src.is_file():
            cand = sorted(COMFY_OUTPUT.rglob(f"ov_up_{p.stem}*.mp4"),
                          key=lambda x: x.stat().st_mtime, reverse=True)
            if not cand:
                log(f"FAIL {p.name}: produced file not found ({info})")
                return 1
            src = cand[0]
        shutil.copy2(src, dst)
        after = sharpness(dst)
        rows.append((p.name, before, after, elapsed, dst.stat().st_size))
        log(f"OK {p.name}  {elapsed:.1f}s  sharp {before:.0f} → {after:.0f}  "
            f"{dst.stat().st_size/1e6:.1f} MB")

    if rows:
        print("\n== A/B ==")
        print(f"{'file':<10}{'sharp before':>14}{'sharp after':>13}{'×':>7}{'sec':>8}")
        for name, b, a, sec, _sz in rows:
            ratio = (a / b) if b and a else float("nan")
            print(f"{name:<10}{b:>14.0f}{a:>13.0f}{ratio:>7.2f}{sec:>8.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())




