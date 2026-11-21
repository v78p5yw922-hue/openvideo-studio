"""J：镜头取证包（无新依赖版）——挂在 take 落库时算，进 takes.metrics_json。

指标（全部 ffmpeg/ffprobe，秒级）：
  frames / fps / duration      真实帧数（修 EDL 用名义时长那条的同一批数据）
  motion                       首尾帧差 + 采样帧差均值（运镜/动作幅度）
  loudness                     EBU R128（I / LRA / true peak）
  silence_ratio                静音时长占比（对白是否真的发生了）
  seam（合成时算）              相邻片段接缝处的电平差（dB），> 6 dB 报警

为什么先做这些：它们全部零依赖、可复现，且能把"运镜有没有发生""对白有没有出声"
从人眼判断变成数字。ASR（吞字/开口偏差）与人脸 embedding（身份漂移）需要额外依赖，
留作第二步。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

CACHE_DIR = None


def _run(cmd: list[str], timeout: int = 120) -> str:
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return (cp.stderr or "") + (cp.stdout or "")


def probe(path: Path) -> dict:
    """真实帧数/时长/分辨率（ffprobe）。"""
    out = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=nb_frames,r_frame_rate,width,height",
                "-show_entries", "format=duration", "-of", "json", str(path)])
    try:
        d = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        return {}
    st = (d.get("streams") or [{}])[0]
    num, _, den = str(st.get("r_frame_rate") or "24/1").partition("/")
    try:
        fps = round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        fps = 24.0
    return {
        "frames": int(st.get("nb_frames") or 0),
        "fps": fps,
        "duration": round(float((d.get("format") or {}).get("duration") or 0), 3),
        "width": st.get("width"),
        "height": st.get("height"),
    }


def motion(path: Path, frames: int = 6) -> dict:
    """画面变化量：首尾帧差 + 相邻采样帧差的均值（0–1）。"""
    tmp = (CACHE_DIR or Path("/tmp")) / f"metric_{path.stem}"
    tmp.mkdir(parents=True, exist_ok=True)
    info = probe(path)
    dur = info.get("duration") or 1.0
    shots = [round(dur * i / (frames - 1), 3) for i in range(frames)]
    pngs = []
    for i, ts in enumerate(shots):
        dst = tmp / f"{i}.png"
        subprocess.run(["ffmpeg", "-y", "-ss", str(ts), "-i", str(path), "-frames:v", "1",
                        "-vf", "scale=336:192", str(dst)],
                       capture_output=True, text=True, timeout=120)
        if dst.is_file():
            pngs.append(dst)
    if len(pngs) < 2:
        return {"span": -1.0, "step": -1.0}

    def diff(a: Path, b: Path) -> float:
        out = _run(["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b), "-lavfi",
                    "blend=all_mode=difference,signalstats,"
                    "metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-"])
        for line in out.splitlines():
            if "YAVG" in line:
                return round(float(line.split("=")[-1]) / 255, 4)
        return -1.0

    steps = [diff(pngs[i], pngs[i + 1]) for i in range(len(pngs) - 1)]
    steps = [s for s in steps if s >= 0]
    return {"span": diff(pngs[0], pngs[-1]), "step": round(sum(steps) / len(steps), 4) if steps else -1.0}


def audio_stats(path: Path) -> dict:
    """响度 + 静音占比。"""
    out = _run(["ffmpeg", "-hide_banner", "-i", str(path),
                "-af", "loudnorm=print_format=json", "-f", "null", "-"])
    lo = {}
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S)
    if m:
        try:
            lo = json.loads(m.group(0))
        except json.JSONDecodeError:
            lo = {}
    sil = _run(["ffmpeg", "-hide_banner", "-i", str(path),
                "-af", "silencedetect=n=-40dB:d=0.25", "-f", "null", "-"])
    total = probe(path).get("duration") or 0.0
    quiet = sum(float(x) for x in re.findall(r"silence_duration: ([0-9.]+)", sil))
    return {
        "lufs": lo.get("input_i"),
        "lra": lo.get("input_lra"),
        "true_peak": lo.get("input_tp"),
        "silence_s": round(quiet, 2),
        "silence_ratio": round(quiet / total, 3) if total else None,
    }


def compute(path: Path) -> dict:
    """一条 take 的全部指标（任何一个环节失败都不影响落库）。"""
    path = Path(path)
    out: dict = {"file": path.name}
    if not path.is_file():
        return {"file": path.name, "error": "missing"}
    for fn in (probe, motion, audio_stats):
        try:
            out.update(fn(path))
        except Exception as e:  # noqa: BLE001
            out[f"error_{fn.__name__}"] = str(e)[:120]
    # 运镜判读（用画面变化量做粗分类，只做提示）
    span, step = out.get("span", -1), out.get("step", -1)
    if span >= 0 and step >= 0:
        if span < 0.02:
            out["camera_hint"] = "static"
        elif span > 0.10:
            out["camera_hint"] = "moving"
        else:
            out["camera_hint"] = "slight"
    return out
