#!/usr/bin/env python3
"""Finalise a merged drama: burn dialogue, deliver 1080×1920, export a sidecar SRT.

Subtitles are drawn with ``drawtext`` against an explicit CJK font file rather
than libass' ``subtitles`` filter: libass scales SRT styles against a 288-line
PlayRes, which made the text ~7× too large and clipped. ``drawtext`` takes a
pixel font size, so what you specify is what you get.

Usage
-----
    python scripts/finalize.py drama/wuxia_script.json \
        --in output/wuxia_rain_2k.mp4 --out output/wuxia_rain_1080p.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# 想在字幕里保留"谁说的"，把这个环境变量设为 1
SPEAKER_IN_SUBS = os.environ.get("SUBTITLE_SPEAKER", "") == "1"

DIALOGUE_RE = re.compile(r"<d>\s*([^：:<>\n]{1,20})\s*[：:]\s*([^<>\n]{1,200}?)\s*</d>")
SRT_TIME = "{:02d}:{:02d}:{:02d},{:03d}"
DEFAULT_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def log(msg: str) -> None:
    print(f"[finalize] {msg}", flush=True)


def probe(path: Path) -> tuple[int, int, float]:
    cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                         "-show_entries", "format=duration",
                         "-of", "default=noprint_wrappers=1", str(path)],
                        stdout=subprocess.PIPE, text=True)
    w = h = 0
    dur = 0.0
    for line in cp.stdout.splitlines():
        if line.startswith("width="):
            w = int(line.split("=")[1])
        elif line.startswith("height="):
            h = int(line.split("=")[1])
        elif line.startswith("duration="):
            dur = float(line.split("=")[1])
    return w, h, dur


def fmt(sec: float) -> str:
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return SRT_TIME.format(h, m, s, ms)


def segment_durations(seg_dir: Path) -> list[float]:
    """Actual per-shot durations, in shot order."""
    out = []
    for p in sorted(seg_dir.glob("0*.mp4")):
        out.append(probe(p)[2])
    return out


def collect_cues(script: dict,
                 durations: list[float] | None = None) -> list[tuple[float, float, str]]:
    """Build cues on the *real* timeline.

    H3 quantises every shot to 17g+5 frames, so a 6 s shot is really 5.875 s and
    a 7 s shot 7.29 s. Using the script numbers would drift ~1 s by the last shot,
    so callers pass the measured segment durations instead.
    """
    cues: list[tuple[float, float, str]] = []
    t = 0.0
    for idx, sh in enumerate(script.get("shots") or []):
        dur = (durations[idx] if durations and idx < len(durations)
               else float(sh.get("duration_sec") or 0))
        raw = (sh.get("dialogue") or "").strip()
        found = DIALOGUE_RE.findall(raw) if raw else []
        if found:
            each = dur / len(found)
            for k, (speaker, line) in enumerate(found):
                s = t + k * each
                # 字幕只放台词：`<d>小石头→妈妈：台词</d>` 里的"谁说的"是给模型看的，
                # 烧进画面会变成"小石头→ 妈妈：…"，观众不需要。
                who = (speaker or "").split("→")[0].strip()
                text = line if not SPEAKER_IN_SUBS else f"{who}：{line}"
                cues.append((s, s + each, text))
        t += dur
    return cues


def wrap_cjk(text: str, max_chars: int) -> list[str]:
    """Greedy wrap that keeps CJK punctuation off the start of a line."""
    lines, cur = [], ""
    for ch in text:
        if len(cur) >= max_chars:
            lines.append(cur)
            cur = ""
        cur += ch
    if cur:
        lines.append(cur)
    return lines or [text]


def esc_drawtext(text: str) -> str:
    """Escape for the drawtext filter (colons, backslashes, quotes, percent)."""
    out = text.replace("\\", r"\\\\").replace(":", r"\\:").replace("'", r"\\'")
    out = out.replace("%", r"\\%")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Burn dialogue subtitles, deliver 1080x1920")
    ap.add_argument("script")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", dest="dst", required=True)
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--font-size", type=int, default=44)
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--segments", default=None,
                    help="segment dir; cue times come from real durations")
    ap.add_argument("--no-subs", action="store_true")
    args = ap.parse_args(argv)

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    src = Path(args.src).expanduser().resolve()
    dst = Path(args.dst).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    w, h, dur = probe(src)
    log(f"input {src.name}: {w}x{h} {dur:.2f}s")

    durations = None
    if args.segments:
        seg_dir = Path(args.segments).expanduser().resolve()
        durations = segment_durations(seg_dir)
        log(f"using {len(durations)} measured durations: "
            f"{[round(d, 2) for d in durations]}")
    cues = collect_cues(script, durations)

    # sidecar SRT next to the delivery, always
    srt_path = dst.with_suffix(".srt")
    if cues:
        blocks = []
        for i, (s, e, text) in enumerate(cues, start=1):
            blocks.append(f"{i}\n{fmt(s)} --> {fmt(e)}\n{text}\n")
        srt_path.write_text("\n".join(blocks), encoding="utf-8")
        log(f"{len(cues)} cue(s) → {srt_path.name}")

    vf = [f"scale={args.width}:{args.height}:force_original_aspect_ratio=decrease:flags=lanczos",
          f"pad={args.width}:{args.height}:(ow-iw)/2:(oh-ih)/2:color=black"]
    if cues and not args.no_subs:
        font = Path(args.font)
        if not font.is_file():
            log(f"font missing: {font} — drawing without text")
        else:
            max_chars = max(8, int(args.width * 0.78 / (args.font_size * 1.02)))
            line_h = int(args.font_size * 1.35)
            for s, e, text in cues:
                wrapped = wrap_cjk(text, max_chars)
                for k, line in enumerate(wrapped):
                    # bottom-aligned block: last line sits at height-110
                    y = args.height - 110 - (len(wrapped) - 1 - k) * line_h
                    vf.append(
                        f"drawtext=fontfile='{font}':text='{esc_drawtext(line)}':"
                        f"fontsize={args.font_size}:fontcolor=white:borderw=2:"
                        f"bordercolor=black@0.85:x=(w-text_w)/2:y={y}:"
                        f"enable='between(t,{max(0.0, s - 0.05):.3f},{e:.3f})'"
                    )

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", ",".join(vf),
           "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0",
           "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(dst)]
    cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0:
        log("encode failed")
        log((cp.stdout or "")[-600:])
        return 1

    w2, h2, d2 = probe(dst)
    log(f"delivered {dst.name}: {w2}x{h2} {d2:.2f}s {dst.stat().st_size/1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
