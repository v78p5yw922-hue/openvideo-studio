#!/usr/bin/env python3
"""Acceptance measurements for a rendered drama project.

Produces numbers for the criteria that CAN be measured mechanically, and is
explicit about the ones that still need human eyes/ears:

  * shot duration vs script (tolerance)
  * audio presence + loudness (dialogue/soundscape are in the same pass)
  * cut continuity (SSIM between last frame of N and first frame of N+1)
  * palette drift (dominant colours of the first frame of each shot)

Usage:
    python scripts/acceptance.py drama/demo/drama_script.json --segments segments/wheat_1990
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np


def sh(cmd: list[str]) -> str:
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return cp.stdout


def probe(path: Path) -> dict:
    out = sh(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-show_entries", "stream=codec_type,width,height,channels,sample_rate",
              "-of", "json", str(path)])
    d = json.loads(out or "{}")
    dur = float((d.get("format") or {}).get("duration") or 0)
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    return {"duration": dur, "width": v.get("width"), "height": v.get("height"),
            "has_audio": a is not None, "channels": (a or {}).get("channels"),
            "sample_rate": (a or {}).get("sample_rate")}


def frame(video: Path, out: Path, where: str) -> Path:
    if where == "first":
        sh(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1", "-update", "1", str(out)])
    else:
        info = probe(video)
        seek = max(0.0, info["duration"] - 0.12)
        sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.3f}", "-i", str(video),
            "-frames:v", "1", "-update", "1", str(out)])
    return out


def ssim(a: Path, b: Path) -> float:
    out = sh(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi", "ssim", "-f", "null", "-"])
    m = re.search(r"All:([0-9.]+)", out)
    return float(m.group(1)) if m else float("nan")


def palette(img: Path, k: int = 4) -> list[list[int]]:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(img), "-vf", "scale=48:27",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         stdout=subprocess.PIPE).stdout
    arr = np.frombuffer(raw, dtype=np.uint8)
    if arr.size < 48 * 27 * 3:
        return []
    px = arr.reshape(-1, 3).astype(np.float32)
    # crude k-means (deterministic init)
    rng = np.random.default_rng(7)
    cent = px[rng.choice(len(px), k, replace=False)]
    for _ in range(12):
        d = ((px[:, None, :] - cent[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        for j in range(k):
            sel = px[lab == j]
            if len(sel):
                cent[j] = sel.mean(axis=0)
    counts = np.bincount(lab, minlength=k)
    order = np.argsort(-counts)
    return [[int(c) for c in cent[j]] for j in order]


def palette_distance(p: list[list[int]], q: list[list[int]]) -> float:
    """Mean nearest-colour distance (0 = identical palettes, 441 = opposite)."""
    if not p or not q:
        return float("nan")
    a, b = np.array(p, float), np.array(q, float)
    d = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    return float((d.min(axis=1).mean() + d.min(axis=0).mean()) / 2)


def loudness(video: Path) -> dict:
    out = sh(["ffmpeg", "-hide_banner", "-i", str(video), "-af", "volumedetect", "-f", "null", "-"])
    mean = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", out)
    peak = re.search(r"max_volume:\s*(-?[0-9.]+) dB", out)
    sil = sh(["ffmpeg", "-hide_banner", "-i", str(video), "-af",
              "silencedetect=noise=-35dB:d=0.3", "-f", "null", "-"])
    silences = len(re.findall(r"silence_start", sil))
    return {"mean_db": float(mean.group(1)) if mean else None,
            "peak_db": float(peak.group(1)) if peak else None,
            "silence_gaps": silences}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script")
    ap.add_argument("--segments", required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    script = json.loads(Path(args.script).expanduser().read_text(encoding="utf-8"))
    seg_dir = Path(args.segments).expanduser().resolve()
    segs = sorted(p for p in seg_dir.glob("*.mp4") if re.match(r"^\d{4}\.mp4$", p.name))
    shots = script.get("shots") or []
    if not segs:
        print("no segments")
        return 2

    results = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        frames = {}
        for i, p in enumerate(segs, start=1):
            info = probe(p)
            info["file"] = p.name
            info["shot_id"] = (shots[i - 1] or {}).get("shot_id") if i <= len(shots) else None
            info["script_duration"] = (shots[i - 1] or {}).get("duration_sec") if i <= len(shots) else None
            info["duration_delta"] = (None if info["script_duration"] is None
                                      else round(info["duration"] - float(info["script_duration"]), 3))
            info["audio"] = loudness(p) if info["has_audio"] else None
            frames[i] = (frame(p, tmp / f"{i}_first.png", "first"),
                         frame(p, tmp / f"{i}_last.png", "last"))
            info["palette"] = palette(frames[i][0])
            results.append(info)

        cuts = []
        for i in range(len(segs) - 1):
            s = ssim(frames[i + 1][1], frames[i + 2][0])
            cuts.append({"boundary": f"{segs[i].stem}->{segs[i+1].stem}", "ssim": round(s, 4)})

        pal = []
        for i in range(len(results) - 1):
            d = palette_distance(results[i]["palette"], results[i + 1]["palette"])
            pal.append({"boundary": f"{segs[i].stem}->{segs[i+1].stem}", "palette_dist": round(d, 1)})

    summary = {
        "segments": len(segs),
        "total_duration": round(sum(r["duration"] for r in results), 2),
        "script_duration": sum(float(s.get("duration_sec") or 0) for s in shots),
        "max_duration_delta": max((abs(r["duration_delta"] or 0) for r in results), default=0),
        "all_have_audio": all(r["has_audio"] for r in results),
        "shots_with_dialogue": sum(1 for s in shots if (s.get("dialogue") or "").strip()),
        "mean_cut_ssim": round(float(np.mean([c["ssim"] for c in cuts])), 4) if cuts else None,
        "mean_palette_dist": round(float(np.mean([p["palette_dist"] for p in pal])), 1) if pal else None,
    }

    if args.json:
        print(json.dumps({"summary": summary, "segments": results,
                          "cuts": cuts, "palette": pal}, ensure_ascii=False, indent=2))
        return 0

    print(f"== 验收测量 · {script.get('project_id')} ==")
    print(f"片段 {summary['segments']} · 总时长 {summary['total_duration']}s "
          f"(脚本 {summary['script_duration']}s)")
    print(f"{'镜头':<10}{'时长':>7}{'偏差':>8}{'音频':>6}{'响度':>10}{'静音段':>7}")
    for r in results:
        a = r["audio"] or {}
        print(f"{r['shot_id'] or r['file']:<10}{r['duration']:>7.2f}{r['duration_delta']:>8.2f}"
              f"{'yes' if r['has_audio'] else 'NO':>6}{a.get('mean_db', '-'):>10}{a.get('silence_gaps', '-'):>7}")
    print(f"\n切口连续性 SSIM: " + ", ".join(f"{c['boundary']}={c['ssim']}" for c in cuts))
    print(f"主色调漂移: " + ", ".join(f"{p['boundary']}={p['palette_dist']}" for p in pal))
    print(f"\n对白镜头数: {summary['shots_with_dialogue']} / {len(shots)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
