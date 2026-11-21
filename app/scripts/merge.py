#!/usr/bin/env python3
"""Stage 3 — normalise, sync-align, colour-align then concat segments.

Three passes, cheapest first (quality preserved whenever possible):

  1. segment already matches the canonical spec  -> used as-is (no re-encode)
  2. video matches but audio/offset does not     -> video copy, audio re-encoded
  3. video does not match                        -> full re-encode (NVENC/x264)

Canonical spec: resolution from segment 1, ``--fps`` (24), AAC stereo at the
sample rate of segment 1, yuv420p, and **both streams pinned to PTS 0** (this is
the audio/video sync guarantee — see ``sync`` below).

Sync (deterministic, no ComfyUI node involved)
----------------------------------------------
For every segment we read the video and audio stream ``start_time`` with
ffprobe.  If ``|audio_start - video_start| > --sync-tol`` the segment is pushed
through an audio-alignment path:

    -af "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS"
    -avoid_negative_ts make_zero

After the concat we measure the merged file again; if it still deviates beyond
``--sync-tol`` we re-mux the whole master (``-c:v copy``) with the same audio
resampler.  The final report prints every measured offset, so the claim is
verifiable from the command output alone.

Colour/luma alignment (global, after concat)
--------------------------------------------
Per-shot grading is wrong: drift accumulates before decode.  What works is one
global correction on the finished master.  We compute each segment's median
luma (and colour balance) and, in ``--grade median|first`` mode, apply a single
``eq``/``colorlevels`` pass to the merged master so every segment lands on the
same target.  ``--grade none`` disables it.

Usage
-----
    python scripts/merge.py --segments segments/x --out output/x.mp4
    python scripts/merge.py --segments segments/x --out x.mp4 --dry-run
    python scripts/merge.py --segments segments/x --out x.mp4 --grade none
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

AUDIO_ARGS = ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
SYNC_AUDIO_FILTER = "aresample=async=1:first_pts=0,asetpts=PTS-STARTPTS"


def log(msg: str) -> None:
    print(f"[merge {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=timeout)


def nvenc_args(cq: int) -> list[str]:
    return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr",
            "-cq", str(cq), "-b:v", "0", "-pix_fmt", "yuv420p"]


X264_ARGS = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]


def probe(path: Path) -> dict | None:
    cp = run(["ffprobe", "-v", "error",
              "-show_entries", "format=duration,size,start_time",
              "-show_entries", "stream=codec_type,codec_name,width,height,"
                               "r_frame_rate,sample_rate,channels,pix_fmt,start_time",
              "-of", "json", str(path)])
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return None
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        return None
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    def f(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    return {
        "duration": f(fmt.get("duration")),
        "size": int(fmt.get("size") or 0),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": v.get("r_frame_rate", "0/1"),
        "pix_fmt": v.get("pix_fmt") or "",
        "vcodec": v.get("codec_name") or "",
        "v_start": f(v.get("start_time")),
        "has_audio": a is not None,
        "sample_rate": int(a.get("sample_rate") or 0) if a else 0,
        "channels": int(a.get("channels") or 0) if a else 0,
        "acodec": (a or {}).get("codec_name") or "",
        "a_start": f((a or {}).get("start_time")),
    }


def av_delta(info: dict | None) -> float | None:
    if not info or not info["has_audio"]:
        return None
    return info["a_start"] - info["v_start"]


def median_luma(path: Path) -> float | None:
    """Median luma (0-255) over a sampled, downscaled decode."""
    # metadata=print emits at INFO level, so -v error would hide every sample
    cp = run(["ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
              "-vf", "fps=2,scale=96:-1,signalstats,metadata=print:key=lavfi.signalstats.YAVG",
              "-f", "null", "-"])
    vals = [float(m) for m in re.findall(r"YAVG=([0-9.]+)", cp.stdout or "")]
    if not vals:
        return None
    vals.sort()
    return vals[len(vals) // 2]


def nvenc_available() -> bool:
    """Real probe; 128x128 is below NVENC's minimum frame size and gives a false NO."""
    if not shutil.which("ffmpeg"):
        return False
    cp = run(["ffmpeg", "-hide_banner", "-encoders"])
    if "h264_nvenc" not in (cp.stdout or ""):
        return False
    cp = run(["ffmpeg", "-hide_banner", "-v", "error",
              "-f", "lavfi", "-i", "testsrc=size=640x360:rate=24:duration=0.3",
              "-c:v", "h264_nvenc", "-f", "null", "-"])
    return cp.returncode == 0


def fps_matches(src: str, want: int) -> bool:
    if not src or "/" not in src:
        return False
    try:
        num, den = src.split("/")
        return den not in ("0", "") and abs(int(num) / int(den) - want) < 0.01
    except (ValueError, ZeroDivisionError):
        return False


def normalise(src: Path, dst: Path, w: int, h: int, fps: int, sr: int,
              video_copy: bool, nvenc: bool, cq: int, timeout: float) -> tuple[bool, str]:
    info = probe(src)
    if info is None:
        return False, "unreadable"

    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src)]
    if not info["has_audio"]:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={sr}"]

    if video_copy:
        cmd += ["-c:v", "copy"]
    else:
        vf = (
            f"setpts=PTS-STARTPTS,"
            f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={fps},setsar=1,format=yuv420p"
        )
        cmd += ["-vf", vf] + (nvenc_args(cq) if nvenc else X264_ARGS)

    if not info["has_audio"]:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
        cmd += ["-af", SYNC_AUDIO_FILTER]
    else:
        cmd += ["-af", SYNC_AUDIO_FILTER]

    cmd += AUDIO_ARGS + ["-ar", str(sr), "-avoid_negative_ts", "make_zero",
                         "-movflags", "+faststart", str(dst)]
    try:
        cp = run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg timeout"
    if cp.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        tail = (cp.stdout or "").strip().splitlines()
        return False, tail[-1] if tail else "ffmpeg failed"
    return True, "ok"


def remux_audio_fix(src: Path, dst: Path, timeout: float) -> bool:
    """Whole-master A/V rescue: copy video, re-time audio to PTS 0."""
    cp = run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
              "-c:v", "copy", "-af", SYNC_AUDIO_FILTER,
              "-c:a", "aac", "-b:a", "192k", "-avoid_negative_ts", "make_zero",
              "-movflags", "+faststart", str(dst)], timeout=timeout)
    return cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def grade_part(src: Path, dst: Path, target_luma: float, timeout: float) -> bool:
    """Re-encode one part with an eq brightness that lands its median luma on target."""
    cur = median_luma(src)
    if cur is None:
        return False
    delta = max(-0.2, min(0.2, (target_luma - cur) / 255.0))
    cp = run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
              "-vf", f"eq=brightness={delta:.4f},format=yuv420p",
              "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0",
              "-c:a", "copy", "-movflags", "+faststart", str(dst)], timeout=timeout)
    return cp.returncode == 0 and dst.exists() and dst.stat().st_size > 1024


def align_luma(parts: list[Path], norm_dir: Path, mode: str, tol: float,
               timeout: float) -> tuple[list[Path], list[float], list[float], float | None]:
    """Measure every part, then (only if the spread is visible) land them all on one target.

    Global judgement, local correction: the *decision* uses the whole timeline, the
    *correction* is applied per part so each shot ends on the same median luma —
    a single global gain on the master cannot equalise shot-to-shot differences.
    """
    before = [median_luma(p) for p in parts]
    known = [x for x in before if x is not None]
    if not known:
        return parts, before, before, None
    spread = max(known) - min(known)
    if mode == "none" or spread <= tol:
        log(f"luma spread {spread:.1f} ≤ tolerance {tol:.1f} — no grading applied")
        return parts, before, before, spread
    target = known[0] if mode == "first" else sorted(known)[len(known) // 2]
    log(f"luma spread {spread:.1f} > {tol:.1f} — landing every part on {target:.1f}")
    out: list[Path] = []
    for i, (p, luma) in enumerate(zip(parts, before), start=1):
        if luma is None or abs(luma - target) <= 1.0:
            out.append(p)
            continue
        dst = norm_dir / f"{i:04d}.grade.mp4"
        if grade_part(p, dst, target, timeout):
            out.append(dst)
        else:
            log(f"  grade failed for {p.name}; keeping original")
            out.append(p)
    after = [median_luma(p) for p in out]
    return out, before, after, spread


def collect(segments_dir: Path, pattern: str = r"^\d{4}\.mp4$") -> list[Path]:
    rx = re.compile(pattern)
    return sorted(p for p in segments_dir.iterdir() if p.is_file() and rx.match(p.name))


def lay_beds(src: Path, bgm: Path | None, ambience: Path | None,
              bgm_gain: float, amb_gain: float, voice_duck: bool,
              sr: int, timeout: float) -> Path:
    """把整集 BGM / 全局 ambience 铺进成片（原地替换）。

    - 两条床按成片时长循环，尾 1.5s 淡出
    - voice_duck：用台词轨做 sidechain 把 BGM 压下去（不盖对白）
    - 只有固定的两条床，镜与镜之间不会"重头开始"
    """
    dur = (probe(src) or {}).get("duration") or 0.0
    if dur <= 0:
        return src
    out = src.with_name(src.stem + ".mixed" + src.suffix)
    fmt = "aformat=sample_fmts=fltp:channel_layouts=stereo"

    inputs: list[str] = ["-i", str(src)]
    # [voice] 既要进 sidechain 又要进最终 amix → 必须 asplit，否则"matches no streams"
    filt: list[str] = [f"[0:a]{fmt},asplit=2[voice][voice_key]"]
    idx = 0
    if ambience:
        inputs += ["-stream_loop", "-1", "-i", str(ambience)]
        idx += 1
        filt.append(f"[{idx}:a]volume={amb_gain}dB,{fmt},atrim=0:{dur:.3f},"
                    f"afade=t=out:st={max(0.0, dur - 1.5):.3f}:d=1.5[amb]")
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        idx += 1
        filt.append(f"[{idx}:a]volume={bgm_gain}dB,{fmt},atrim=0:{dur:.3f},"
                    f"afade=t=out:st={max(0.0, dur - 1.5):.3f}:d=1.5[bgm_raw]")

    duck = bool(bgm and voice_duck)
    if duck:
        filt.append("[bgm_raw][voice_key]sidechaincompress=threshold=0.05:ratio=6:"
                    "attack=20:release=350[bgm]")
    elif bgm:
        filt.append("[bgm_raw]anull[bgm]")

    mix_in = ["[voice]"]
    if ambience:
        mix_in.append("[amb]")
    if bgm:
        mix_in.append("[bgm]")
    if len(mix_in) == 1:
        return src
    # normalize=0：不按输入数衰减（否则整体响度会掉 ~9 dB）
    filt.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:normalize=0:duration=first:"
                                  "dropout_transition=0,alimiter=limit=0.95[mix]")

    cp = run(["ffmpeg", "-y", "-v", "error", *inputs,
              "-filter_complex", ";".join(filt),
              "-map", "0:v", "-map", "[mix]",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", str(sr),
              "-movflags", "+faststart", str(out)], timeout=timeout)
    if cp.returncode != 0 or not out.is_file():
        log(f"铺轨失败（保留原片）：{(cp.stdout or '').strip()[-300:]}")
        out.unlink(missing_ok=True)
        return src
    out.replace(src)
    log(f"已铺音频床：bgm={'有' if bgm else '无'} ambience={'有' if ambience else '无'} "
        f"台词侧链={'开' if duck else '关'}")
    return src


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Normalise + sync-align + grade then concat H3 segments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--segments", default="segments", help="dir with NNNN.mp4 segments")
    ap.add_argument("--out", default="output/final.mp4", help="merged output file")
    ap.add_argument("--pattern", default=r"^\d{4}\.mp4$", help="segment filename regex")
    ap.add_argument("--fps", type=int, default=24, help="target frame rate")
    ap.add_argument("--sample-rate", type=int, default=None,
                    help="target audio sample rate (default: from segment 1)")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--no-nvenc", action="store_true")
    ap.add_argument("--cq", type=int, default=23, help="NVENC constant quality")
    ap.add_argument("--reencode-all", action="store_true")
    ap.add_argument("--sync-tol", type=float, default=0.05,
                    help="max |audio_start - video_start| before realigning (s)")
    ap.add_argument("--grade", choices=["none", "median", "first"], default="median",
                    help="luma target: median of all parts, first part, or none")
    ap.add_argument("--luma-tol", type=float, default=6.0,
                    help="only grade when luma spread exceeds this (0-255 scale)")
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--keep-temp", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bgm", default="", help="整集 BGM 文件（循环铺满整片）")
    ap.add_argument("--bgm-gain", type=float, default=-18.0, help="BGM 增益 dB")
    ap.add_argument("--ambience", default="", help="全局环境声文件（循环铺满整片）")
    ap.add_argument("--ambience-gain", type=float, default=-26.0, help="环境声增益 dB")
    ap.add_argument("--voice-duck", action="store_true", help="台词时把 BGM 压低")
    args = ap.parse_args(argv)

    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not found", file=sys.stderr)
        return 2

    seg_dir = Path(args.segments).expanduser().resolve()
    if not seg_dir.is_dir():
        print(f"segments dir not found: {seg_dir}", file=sys.stderr)
        return 2
    segs = collect(seg_dir, args.pattern)
    if not segs:
        print(f"no segments matching {args.pattern} in {seg_dir}", file=sys.stderr)
        return 2

    first = probe(segs[0])
    if first is None:
        print(f"cannot probe first segment: {segs[0]}", file=sys.stderr)
        return 2
    w = args.width or first["width"]
    h = args.height or first["height"]
    sr = args.sample_rate or first["sample_rate"] or 48000

    nvenc = (not args.no_nvenc) and nvenc_available()
    log(f"{len(segs)} segment(s); target {w}x{h} @ {args.fps}fps, {sr}Hz stereo")
    log(f"re-encode encoder: {'h264_nvenc (cq=' + str(args.cq) + ')' if nvenc else 'libx264 (crf=20)'}")
    log(f"sync tolerance ±{args.sync_tol:.3f}s · grade={args.grade}")

    plan: list[tuple[Path, str, float | None]] = []
    log(f"{'segment':<14}{'size':>12}{'fps':>7}{'a/v Δ':>9}  action")
    for p in segs:
        info = probe(p)
        if info is None:
            log(f"  {p.name}: UNREADABLE — abort")
            return 2
        delta = av_delta(info)
        video_ok = (not args.reencode_all and info["width"] == w and info["height"] == h
                    and fps_matches(info["fps"], args.fps) and info["pix_fmt"] == "yuv420p")
        audio_ok = (info["has_audio"] and info["sample_rate"] == sr
                    and info["channels"] == 2)
        sync_ok = delta is None or abs(delta) <= args.sync_tol
        if video_ok and audio_ok and sync_ok:
            mode = "copy"
        elif video_ok:
            mode = "audio"
        else:
            mode = "full"
        plan.append((p, mode, delta))
        d = "—" if delta is None else f"{delta:+.3f}s"
        log(f"  {p.name:<14}{info['width']}x{info['height']:<7}{info['fps']:>7}{d:>9}  {mode}")

    if args.dry_run:
        log("dry-run: nothing encoded")
        return 0

    norm_dir = seg_dir / ".norm"
    norm_dir.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    parts: list[Path] = []
    t_start = time.time()
    for i, (p, mode, _delta) in enumerate(plan, start=1):
        if mode == "copy":
            parts.append(p)
            log(f"[{i}/{len(plan)}] copy     {p.name} (canonical, in sync)")
            continue
        dst = norm_dir / f"{i:04d}.norm.mp4"
        sidecar = norm_dir / f"{i:04d}.json"
        sig = {"w": w, "h": h, "fps": args.fps, "sr": sr, "mode": mode,
               "src": p.name, "src_size": p.stat().st_size}
        if dst.exists() and sidecar.exists():
            try:
                if json.loads(sidecar.read_text()) == sig:
                    log(f"[{i}/{len(plan)}] reuse    {dst.name}")
                    parts.append(dst)
                    continue
            except json.JSONDecodeError:
                pass
        log(f"[{i}/{len(plan)}] {'sync' if mode == 'audio' else 'encode'} {p.name} -> {dst.name}")
        ok, why = normalise(p, dst, w, h, args.fps, sr, video_copy=(mode == "audio"),
                            nvenc=nvenc, cq=args.cq, timeout=args.timeout)
        if not ok:
            log(f"  FAILED: {why}")
            return 1
        sidecar.write_text(json.dumps(sig))
        parts.append(dst)

    list_file = norm_dir / "concat.txt"
    list_file.write_text("".join(f"file '{f.as_posix()}'\n" for f in parts), encoding="utf-8")
    expected = sum((probe(f) or {}).get("duration", 0.0) for f in parts)
    log(f"concat {len(parts)} part(s), expected duration {expected:.2f}s")

    cp = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
              "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out_path)],
             timeout=args.timeout)
    result = probe(out_path) if out_path.exists() else None
    stream_ok = result is not None and abs(result["duration"] - expected) <= max(0.5, expected * 0.01)

    if cp.returncode != 0 or not stream_ok:
        log("stream-copy concat failed or duration mismatch — re-encoding the join")
        enc = nvenc_args(args.cq) if nvenc else X264_ARGS
        cp = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                  "-i", str(list_file)] + enc + AUDIO_ARGS +
                 ["-af", SYNC_AUDIO_FILTER, "-ar", str(sr),
                  "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(out_path)],
                 timeout=args.timeout)
        if cp.returncode != 0:
            log(f"re-encoded concat failed: {(cp.stdout or '').strip()[-400:]}")
            return 1
        result = probe(out_path)

    md = av_delta(result) if result else None
    log(f"master a/v Δ = {'n/a' if md is None else f'{md:+.4f}s'} (tolerance ±{args.sync_tol:.3f}s)")

    # ---- luma alignment: judge globally, correct per part, then re-join ----
    graded, before, after, spread = align_luma(parts, norm_dir, args.grade,
                                               args.luma_tol, args.timeout)
    if graded != parts:
        parts = graded
        list_file.write_text("".join(f"file '{f.as_posix()}'\n" for f in parts), encoding="utf-8")
        cp2 = run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                   "-i", str(list_file), "-c", "copy", "-movflags", "+faststart", str(out_path)],
                  timeout=args.timeout)
        if cp2.returncode == 0:
            result = probe(out_path) or result
        else:
            log("re-join after grading failed — keeping previous master")
        log(f"luma after: {[None if x is None else round(x, 1) for x in after]}")

    # ---- final sync gate (after grading, so a re-join cannot undo it) ----
    # Only accept a rescue that measurably improves the offset: an unconditional
    # re-mux can make it worse (AAC priming is ~1024 samples = 32 ms at 32 kHz).
    if result is not None:
        md = av_delta(result)
        if md is not None and abs(md) > args.sync_tol:
            log(f"final drift {md:+.4f}s > ±{args.sync_tol:.3f}s — trying audio re-mux")
            tmp = out_path.with_suffix(".sync.mp4")
            if remux_audio_fix(out_path, tmp, args.timeout):
                cand = av_delta(probe(tmp))
                if cand is not None and abs(cand) < abs(md):
                    tmp.replace(out_path)
                    result = probe(out_path) or result
                    log(f"re-mux improved: {md:+.4f}s → {cand:+.4f}s")
                else:
                    tmp.unlink(missing_ok=True)
                    log(f"re-mux rejected ({md:+.4f}s → {cand if cand is None else f'{cand:+.4f}s'}); keeping original")
            else:
                tmp.unlink(missing_ok=True)
                log("WARNING: audio re-mux failed; master left untouched")

    # ---- 整集音频床（模型逐镜不生成配乐，这里统一铺）----
    if args.bgm or args.ambience:
        out_path = lay_beds(out_path,
                            Path(args.bgm).expanduser() if args.bgm else None,
                            Path(args.ambience).expanduser() if args.ambience else None,
                            args.bgm_gain, args.ambience_gain, args.voice_duck,
                            sr, args.timeout)
        result = probe(out_path) or result

    if not args.keep_temp:
        shutil.rmtree(norm_dir, ignore_errors=True)

    elapsed = time.time() - t_start
    if result:
        log(f"DONE {out_path}  {result['width']}x{result['height']} "
            f"{result['duration']:.2f}s  {result['size'] / 1e6:.2f} MB  in {elapsed:.1f}s")
        log(f"final a/v Δ = {av_delta(result):+.4f}s" if av_delta(result) is not None else "final a/v Δ = n/a")
    else:
        log(f"DONE {out_path} in {elapsed:.1f}s (probe failed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())




