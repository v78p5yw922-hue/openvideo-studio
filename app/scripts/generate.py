#!/usr/bin/env python3
"""Stage 2 — batch generation with resume + first/last-frame chaining.

Two upgrades over the plain batch runner
----------------------------------------
1. **Chained shots (首尾帧链式)**
   Shot 1 is text-to-video. After it finishes, its LAST frame is extracted and
   fed to shot 2 as ``--mode i2v --first-frame``; shot 2's last frame feeds
   shot 3, and so on. That is the same mechanism the CLI's own multi-shot
   pipeline uses (``Shot.first_frame`` → ComfyUI ``load_firstframe``), so each
   cut starts from the exact pixels the previous shot ended on.

2. **Structured prompt headers (角色/场景)**
   ``prompts.txt`` may start with header lines::

       角色: 一个皮肤黝黑、脖子上搭着白毛巾的90年代中国北方农民
       场景: 金色的麦田，夏日清晨
       Wide shot of the wheat field at dawn ...
       Medium shot of the farmer swinging a sickle ...

   Header lines are folded into a fixed block that is prepended to EVERY shot
   prompt, so the character/scene stay specified in each shot instead of being
   implicitly dropped after the first one. English ``character:`` / ``scene:``
   keys work too.

Resume contract
---------------
A segment counts as done only when the final ``NNNN.mp4`` exists, is >=
``--min-bytes``, parses with ffprobe, and its duration is within ``--dur-tol``
of the requested duration. The chain frame is rebuilt from an already-finished
segment when the PNG is missing, so resuming never breaks the chain.

Usage
-----
    python scripts/generate.py --prompts segments/shot/prompts.txt --duration 5
    python scripts/generate.py --prompts ... --no-chain        # pure t2v
    python scripts/generate.py --prompts ... --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = REPO_ROOT / ".venv"
OPEN_VIDEO = VENV_DIR / "bin" / "open-video"
VENV_PY = VENV_DIR / "bin" / "python"
COMFY_DIR = REPO_ROOT / "ComfyUI"
DEFAULT_SERVER = os.environ.get("OPEN_VIDEO_COMFYUI", "http://127.0.0.1:8288")

# Default engine flags: none.  ComfyUI's default RAM cache keeps the weights
# paged in, which measured ~7% faster on a 5 s clip (419 s vs 450 s wall).
# It is also the riskier mode: `MemAvailable` bottomed out at ~120 MB on a 40 GB
# WSL during a single 5 s shot.  Pass `--engine-flags --cache-none` for a
# memory-conservative batch (costs the ~30 s/item win but never pressures RAM).
DEFAULT_ENGINE_FLAGS = ""

# "角色: xxx" / "场景：xxx" / "character: xxx" / "scene: xxx"
HEADER_RE = re.compile(r"^\s*(角色|人物|场景|环境|character|scene|subject|setting)\s*[:：]\s*(.+)$",
                       re.IGNORECASE)

# optional per-shot directive: "[cut]" forces a hard cut (t2v), "[chain]" forces i2v
SHOT_TAG_RE = re.compile(r"^\s*\[(cut|hard|t2v|chain|i2v)\]\s*", re.IGNORECASE)


def log(msg: str) -> None:
    print(f"[gen {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=timeout)


# --------------------------------------------------------------------------- #
# ffprobe / frames
# --------------------------------------------------------------------------- #
def ffprobe_info(path: Path) -> dict | None:
    if not shutil.which("ffprobe"):
        return None
    try:
        cp = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-show_entries", "stream=codec_type,width,height", "-of", "json", str(path)],
                 timeout=60)
    except Exception:
        return None
    if cp.returncode != 0:
        return None
    try:
        data = json.loads(cp.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        return None
    vs = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    try:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    return {"duration": dur, "width": int(vs.get("width") or 0), "height": int(vs.get("height") or 0)}


def extract_last_frame(video: Path, out_png: Path) -> bool:
    """Grab the final frame of ``video`` as a lossless PNG (the chain hand-off)."""
    if not shutil.which("ffmpeg"):
        return False
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.unlink(missing_ok=True)
    info = ffprobe_info(video)
    dur = (info or {}).get("duration") or 0.0
    # seek slightly before the end, then take exactly one frame
    for seek in (max(0.0, dur - 0.12), max(0.0, dur - 0.4), 0.0):
        cp = run(["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.3f}", "-i", str(video),
                  "-frames:v", "1", "-update", "1", str(out_png)], timeout=120)
        if cp.returncode == 0 and out_png.exists() and out_png.stat().st_size > 1024:
            return True
    return False


def sha256_of(path: Path, block: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def engine_healthy(server: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(f"{server}/system_stats", timeout=timeout) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


class VramSampler:
    def __init__(self, interval: float = 0.5):
        self.interval = interval
        self.peak = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._has_smi = shutil.which("nvidia-smi") is not None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                cp = run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         timeout=5)
                if cp.returncode == 0:
                    for line in cp.stdout.splitlines():
                        if line.strip().isdigit():
                            self.peak = max(self.peak, int(line.strip()))
            except Exception:
                pass
            self._stop.wait(self.interval)

    def __enter__(self) -> "VramSampler":
        if self._has_smi:
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)


# --------------------------------------------------------------------------- #
# engine lifecycle
# --------------------------------------------------------------------------- #
def start_engine(server: str, flags: str, log_path: Path, wait_s: int = 300) -> subprocess.Popen | None:
    if not COMFY_DIR.joinpath("main.py").is_file():
        log(f"ERROR: no ComfyUI at {COMFY_DIR}")
        return None
    cmd = [str(VENV_PY), "main.py", "--listen", "127.0.0.1", "--port", server.rsplit(":", 1)[-1]]
    cmd += [f for f in flags.split() if f]
    log(f"starting engine: {' '.join(cmd)}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("ab")
    proc = subprocess.Popen(cmd, cwd=str(COMFY_DIR), stdout=fh, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if proc.poll() is not None:
            log(f"ERROR: engine exited early (rc={proc.returncode}); see {log_path}")
            return None
        if engine_healthy(server):
            log(f"engine healthy after {time.time() - t0:.1f}s (pid {proc.pid})")
            return proc
        time.sleep(1)
    log("ERROR: engine did not become healthy in time")
    return None


def stop_engine(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
    log("engine stopped")


# --------------------------------------------------------------------------- #
# prompt parsing / building
# --------------------------------------------------------------------------- #
def parse_prompts(path: Path) -> tuple[dict[str, str], list[tuple[int, str, str]]]:
    """Return (headers, [(line_no, shot_text, mode)]).

    Header lines (角色/场景/character/scene) are collected, not treated as shots.
    A leading ``[cut]`` (or [hard]/[t2v]) marks a deliberate cut; ``[chain]``
    (or [i2v]) forces the hand-off. Empty mode = follow --chain default.
    """
    headers: dict[str, str] = {}
    shots: list[tuple[int, str, str]] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = HEADER_RE.match(line)
        if m:
            key = m.group(1).lower()
            val = m.group(2).strip()
            canon = ("角色" if key in ("角色", "人物", "character", "subject")
                     else "场景")
            if canon in headers:
                headers[canon] = headers[canon] + "；" + val
            else:
                headers[canon] = val
            continue
        mode = ""
        mtag = SHOT_TAG_RE.match(line)
        if mtag:
            tag = mtag.group(1).lower()
            mode = "cut" if tag in ("cut", "hard", "t2v") else "chain"
            line = line[mtag.end():].strip()
        if line:
            shots.append((lineno, line, mode))
    return headers, shots


def build_prefix(headers: dict[str, str]) -> str:
    if not headers:
        return ""
    parts = []
    if "角色" in headers:
        parts.append(f"角色：{headers['角色']}")
    if "场景" in headers:
        parts.append(f"场景：{headers['场景']}")
    return "。".join(parts) + "。" if parts else ""


def compose_prompt(prefix: str, shot: str) -> str:
    return f"{prefix}{shot}" if prefix else shot


def is_done(path: Path, want_dur: float, dur_tol: float, min_bytes: int) -> tuple[bool, str]:
    if not path.exists():
        return False, "missing"
    size = path.stat().st_size
    if size < min_bytes:
        return False, f"too small ({size} B < {min_bytes} B)"
    info = ffprobe_info(path)
    if info is None:
        return False, "unreadable by ffprobe"
    if want_dur > 0 and abs(info["duration"] - want_dur) > dur_tol:
        return False, f"duration {info['duration']:.2f}s vs wanted {want_dur:.2f}s"
    return True, "ok"


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Batch H3 generation: resident engine, resume, first-frame chaining.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    ap.add_argument("--prompts", help="text file: optional 角色/场景 headers, then one shot per line")
    ap.add_argument("--prompt", action="append", help="inline shot prompt (repeatable)")
    ap.add_argument("--out", default="segments", help="output dir for segments")
    ap.add_argument("--duration", type=float, default=5.0, help="seconds per segment")
    ap.add_argument("--model", default="h3")
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--engine", choices=["auto", "reuse", "manage"], default="auto")
    ap.add_argument("--engine-flags", default=DEFAULT_ENGINE_FLAGS)
    ap.add_argument("--keep-engine", action="store_true")
    ap.add_argument("--min-bytes", type=int, default=100 * 1024)
    ap.add_argument("--dur-tol", type=float, default=0.75)
    ap.add_argument("--retries", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--chain", dest="chain", action="store_true", default=True,
                    help="shot N+1 starts from shot N's last frame (i2v)")
    ap.add_argument("--no-chain", dest="chain", action="store_false",
                    help="disable chaining (every shot is pure t2v)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    headers: dict[str, str] = {}
    raw_shots: list[tuple[int, str, str]] = []
    if args.prompts:
        p = Path(args.prompts).expanduser()
        if not p.is_file():
            raise SystemExit(f"prompts file not found: {p}")
        headers, raw_shots = parse_prompts(p)
    for text in (args.prompt or []):
        if text.strip():
            raw_shots.append((0, text.strip(), ""))
    if args.limit:
        raw_shots = raw_shots[: args.limit]
    if not raw_shots:
        print("no prompts given (use --prompts FILE or --prompt TEXT)", file=sys.stderr)
        return 2
    if not OPEN_VIDEO.is_file():
        print(f"open-video CLI not found at {OPEN_VIDEO}; run scripts/install.sh first", file=sys.stderr)
        return 2

    prefix = build_prefix(headers)
    if headers:
        log(f"headers: 角色={headers.get('角色', '-')[:28]}… 场景={headers.get('场景', '-')[:28]}…")
        log(f"prefix applied to all {len(raw_shots)} shots ({len(prefix)} chars)")
    if not args.chain and len(raw_shots) > 1:
        log("chaining disabled (--no-chain): every shot is t2v")

    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    manifest = out_dir / "manifest.jsonl"
    frames_dir = out_dir / ".frames"

    todo: list[dict] = []
    for idx, (_, shot, tag_mode) in enumerate(raw_shots, start=1):
        final = out_dir / f"{idx:04d}.mp4"
        done, why = (False, "forced") if args.force else is_done(final, args.duration, args.dur_tol, args.min_bytes)
        rec = {"index": idx, "shot": shot, "final": final, "why": why,
               "done": done, "tag": tag_mode}
        if done:
            log(f"SKIP  {final.name}: {why}")
        else:
            log(f"QUEUE {final.name}: {why} — {shot[:56]}")
            todo.append(rec)

    log(f"{len(raw_shots) - len(todo)} skip, {len(todo)} to generate, duration={args.duration}s")
    if args.dry_run:
        prev = None
        for rec in todo:
            chain_this = (args.chain and rec["tag"] != "cut") or rec["tag"] == "chain"
            mode = "i2v" if (chain_this and (prev or rec["index"] > 1)) else "t2v"
            src = f"  ← chain from {prev.name}" if mode == "i2v" and prev else ""
            if rec["tag"]:
                src += f"  [tag:{rec['tag']}]"
            print(f"  {rec['final']}  [{mode}]{src}\n    prompt: {compose_prompt(prefix, rec['shot'])[:120]}")
            prev = rec["final"]
        return 0
    if not todo:
        log("nothing to do — all segments present")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    engine_proc = None
    started_by_us = False
    if args.engine == "manage" or not engine_healthy(args.server):
        if args.engine == "reuse":
            print(f"ERROR: no engine at {args.server} and --engine reuse", file=sys.stderr)
            return 2
        engine_proc = start_engine(args.server, args.engine_flags,
                                   REPO_ROOT / ".cache" / "comfyui-batch.log")
        if engine_proc is None:
            return 2
        started_by_us = True
    else:
        log(f"reusing live engine at {args.server}")

    results: list[dict] = []
    failures: list[tuple[int, str]] = []
    prev_final: Path | None = None  # last frame source for the chain

    try:
        for rec in todo:
            idx, shot, final = rec["index"], rec["shot"], rec["final"]

            if started_by_us and not engine_healthy(args.server):
                log("engine unreachable — restarting before this item")
                stop_engine(engine_proc)
                engine_proc = start_engine(args.server, args.engine_flags,
                                           REPO_ROOT / ".cache" / "comfyui-batch.log")
                if engine_proc is None:
                    log(f"[{idx:04d}] FAIL: engine unavailable, skipping")
                    failures.append((idx, shot))
                    continue

            # ---- chain: use previous shot's last frame as this shot's first ----
            first_frame: Path | None = None
            want_chain = args.chain
            if rec.get("tag") == "cut":
                want_chain = False
            elif rec.get("tag") == "chain":
                want_chain = True
            if want_chain:
                prev_path = prev_final or (out_dir / f"{idx - 1:04d}.mp4")
                if prev_path.exists() and is_done(prev_path, args.duration, args.dur_tol,
                                                  args.min_bytes)[0]:
                    cand = frames_dir / f"{idx - 1:04d}_last.png"
                    if not cand.exists():
                        if extract_last_frame(prev_path, cand):
                            log(f"[{idx:04d}] chain: extracted last frame of {prev_path.name}")
                        else:
                            log(f"[{idx:04d}] WARNING: could not extract last frame; falling back to t2v")
                    if cand.exists():
                        first_frame = cand
                        log(f"[{idx:04d}] chain: first frame ← {prev_path.name}")

            prompt = compose_prompt(prefix, shot)
            work = out_dir / ".tmp" / f"{idx:04d}"
            shutil.rmtree(work, ignore_errors=True)
            work.mkdir(parents=True, exist_ok=True)
            part = work / "segment.mp4"

            attempt = 0
            ok = False
            elapsed = 0.0
            peak = 0
            while attempt <= args.retries and not ok:
                attempt += 1
                shutil.rmtree(work, ignore_errors=True)
                work.mkdir(parents=True, exist_ok=True)
                mode = "i2v" if first_frame else "t2v"
                log(f"[{idx:04d}] attempt {attempt}: {mode} {args.duration}s …")
                cmd = [str(OPEN_VIDEO), prompt, "--model", args.model,
                       "--duration", str(args.duration), "--output", str(part)]
                if first_frame:
                    cmd += ["--mode", "i2v", "--first-frame", str(first_frame)]
                t0 = time.time()
                with VramSampler() as sampler:
                    try:
                        cp = run(cmd, timeout=args.timeout)
                        rc = cp.returncode
                        tail = (cp.stdout or "").strip().splitlines()[-8:]
                    except subprocess.TimeoutExpired:
                        rc, tail = 124, ["timeout"]
                elapsed = time.time() - t0
                peak = sampler.peak

                good, why = is_done(part, args.duration, args.dur_tol, args.min_bytes)
                if rc == 0 and good:
                    final.unlink(missing_ok=True)
                    shutil.move(str(part), str(final))
                    shutil.rmtree(work, ignore_errors=True)
                    ok = True
                    log(f"[{idx:04d}] OK  {elapsed:.1f}s  peak={peak} MiB  -> {final.name}")
                else:
                    log(f"[{idx:04d}] FAIL rc={rc} ({why})")
                    for line in tail:
                        log(f"    | {line}")
                    shutil.rmtree(work, ignore_errors=True)

            if ok:
                prev_final = final
                # warm the NEXT chain frame now, so resume/skip keeps working
                if args.chain:
                    nxt = frames_dir / f"{idx:04d}_last.png"
                    if not nxt.exists():
                        extract_last_frame(final, nxt)

            entry = {
                "index": idx,
                "segment": str(final),
                "shot": shot,
                "prompt": prompt,
                "headers": headers,
                "mode": "i2v" if first_frame else "t2v",
                "first_frame": str(first_frame) if first_frame else None,
                "duration_requested": args.duration,
                "status": "ok" if ok else "failed",
                "attempts": attempt,
                "elapsed_s": round(elapsed, 2),
                "peak_vram_mib": peak,
                "size_bytes": final.stat().st_size if final.exists() else 0,
                "sha256": sha256_of(final) if final.exists() else "",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            probe = ffprobe_info(final) if final.exists() else None
            if probe:
                entry.update({"width": probe["width"], "height": probe["height"],
                              "measured_duration": round(probe["duration"], 3)})
            results.append(entry)
            manifest.parent.mkdir(parents=True, exist_ok=True)
            with manifest.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            if not ok:
                failures.append((idx, shot))
    finally:
        tmp_root = out_dir / ".tmp"
        if tmp_root.is_dir():
            try:
                tmp_root.rmdir()
            except OSError:
                pass
        if started_by_us and not args.keep_engine:
            stop_engine(engine_proc)
        elif started_by_us and args.keep_engine:
            log(f"engine left running (pid {engine_proc.pid if engine_proc else '?'}) — --keep-engine")

    ok_n = len(results) - len(failures)
    total = sum(r["elapsed_s"] for r in results)
    chained = sum(1 for r in results if r["mode"] == "i2v")
    log(f"done: {ok_n} generated ({chained} chained via i2v), {len(failures)} failed, {total:.1f}s")
    if failures:
        for idx, shot in failures:
            log(f"  FAILED {idx:04d}: {shot[:70]}")
        log("re-run the same command to retry only the failures")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


