#!/usr/bin/env python3
"""Render a drama_script.json end to end.

Per shot
--------
1. ``[chain]``  — frame 0 is locked to the previous shot's last frame
   (``MiniMaxH3ImageToVideo`` + ``first_frame``). This is what makes a cut
   pixel-continuous; a soft "scene reference" hint was measured at SSIM
   0.37–0.53, the lock gives ~0.97. After rendering we verify SSIM and retry
   with a new seed until it clears ``--chain-ssim``.
2. ``[cut]``    — identity comes from the cast library: ``H3CastMember`` nodes
   (1–4 single-view images + name + description) feed ``H3CastToVideo``, which
   composes H3's native reference pathway.

Then (optional) a detail pass with the H3 regen2k upsampler, and a merge that
already normalises specs, re-times audio and equalises luma.

Usage
-----
    python scripts/render_drama.py drama/wuxia_script.json --width 768 --height 1344
    python scripts/render_drama.py ... --no-upscale
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _find_comfy() -> Path:
    """ComfyUI 位置：环境变量 > 仓库内 engine/ > 常见位置。

    别人的克隆不会把你的路径写死，所以这里必须是"探测"而不是"假设"。
    """
    import os
    for cand in (os.environ.get("OV_COMFYUI_DIR"),
                 os.environ.get("COMFYUI_DIR"),
                 REPO / "engine" / "ComfyUI",
                 REPO / "ComfyUI",
                 Path.home() / "ComfyUI",
                 "/opt/ComfyUI"):
        if cand and (Path(cand) / "main.py").is_file():
            return Path(cand)
    return Path(os.environ.get("OV_COMFYUI_DIR") or (REPO / "engine" / "ComfyUI"))


COMFY = _find_comfy()
COMFY_INPUT = COMFY / "input"
DEFAULT_SERVER = "http://127.0.0.1:8188"
FPS = 24
WORKFLOW_BASE = REPO / "backends/h3/workflows/h3_t2v_api.json"


def log(msg: str) -> None:
    print(f"[drama {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# http helpers
# --------------------------------------------------------------------------- #
def post_json(url: str, payload: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def stage_image(src: Path) -> str:
    COMFY_INPUT.mkdir(parents=True, exist_ok=True)
    name = "ov_drama_" + re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem)[:40] + src.suffix.lower()
    dst = COMFY_INPUT / name
    if not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime:
        shutil.copy2(src, dst)
    return name


# --------------------------------------------------------------------------- #
# ffmpeg helpers
# --------------------------------------------------------------------------- #
def ffprobe_duration(path: Path) -> float:
    cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(path)],
                        stdout=subprocess.PIPE, text=True)
    try:
        return float(cp.stdout.strip())
    except ValueError:
        return 0.0


def extract_last_frame(video: Path, out_png: Path) -> bool:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    dur = ffprobe_duration(video)
    for seek in (max(0.0, dur - 0.12), max(0.0, dur - 0.4), 0.0):
        cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{seek:.3f}",
                             "-i", str(video), "-frames:v", "1", "-update", "1", str(out_png)],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if cp.returncode == 0 and out_png.exists() and out_png.stat().st_size > 1024:
            return True
    return False


def first_frame_of(video: Path, out_png: Path) -> bool:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-frames:v", "1",
                         "-update", "1", str(out_png)],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return cp.returncode == 0 and out_png.exists() and out_png.stat().st_size > 1024


def frame_ssim(a: Path, b: Path) -> float | None:
    """SSIM between two images, resized to a common raster first.

    The plate and the rendered first frame often differ in height (plate is
    1376, video is 1344), and ffmpeg's ssim filter refuses mismatched sizes —
    that used to return None and silently skip the gate.
    """
    cp = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(a), "-i", str(b),
         "-filter_complex", "[0:v]scale=512:896:force_original_aspect_ratio=increase,"
                            "crop=512:896[a];[1:v]scale=512:896:force_original_aspect_ratio=increase,"
                            "crop=512:896[b];[a][b]ssim",
         "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    m = re.search(r"All:([0-9.]+)", cp.stdout or "")
    return float(m.group(1)) if m else None


class VramSampler:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                cp = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                                     "--format=csv,noheader,nounits"],
                                    stdout=subprocess.PIPE, text=True, timeout=5)
                for tok in cp.stdout.split():
                    if tok.strip().isdigit():
                        self.peak = max(self.peak, int(tok))
            except (OSError, subprocess.SubprocessError):
                pass
            self._stop.wait(2.0)

    def __enter__(self) -> "VramSampler":
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if self._t:
            self._t.join(timeout=3)


# --------------------------------------------------------------------------- #
# prompt
# --------------------------------------------------------------------------- #
def build_prompt(shot: dict, chars: dict, style: str, scene: dict | None) -> str:
    parts: list[str] = []
    if style:
        parts.append(f"{style} style.")
    cam = (shot.get("camera") or "").strip()
    if cam:
        parts.append(f"Camera: {cam}.")
    vp = (shot.get("visual_prompt") or "").strip()
    if vp:
        parts.append(vp if vp.endswith(".") else vp + ".")
    if scene:
        loc = ", ".join(x for x in (scene.get("location"), scene.get("time_of_day"),
                                    scene.get("atmosphere")) if x)
        if loc:
            parts.append(f"Setting: {loc}.")
    for who in shot.get("characters_present") or []:
        c = chars.get(who) or chars.get(str(who))
        if c and (c.get("description") or "").strip():
            parts.append(f"{c.get('name')}: {c['description'].strip()}")
    dlg = (shot.get("dialogue") or "").strip()
    if dlg:
        parts.append(dlg)
    snd = (shot.get("soundscape") or "").strip()
    if snd:
        parts.append(f"Sound: {snd}.")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# workflow
# --------------------------------------------------------------------------- #
def valid_length(dur: float, fps: int = FPS) -> int:
    """H3 only accepts 17g+5 frame counts; pick the nearest legal one."""
    target = dur * fps
    g = max(1, round((target - 5) / 17))
    return 17 * g + 5

def build_workflow(shot: dict, chars: dict, style: str, scene: dict | None,
                   width: int, height: int, steps: int, seed: int,
                   lock_frame: Path | None = None) -> dict:
    wf = json.loads(WORKFLOW_BASE.read_text(encoding="utf-8"))
    dur = float(shot.get("duration_sec") or 5)
    length = valid_length(dur)
    prompt = build_prompt(shot, chars, style, scene)
    prefix = f"ov_drama_{shot.get('shot_id', 'shot')}"

    if lock_frame is not None:
        staged = stage_image(lock_frame)
        wf["lock_img"] = {"class_type": "LoadImage", "inputs": {"image": staged}}
        node = wf["h3_i2v"]
        node["inputs"].update({"prompt": prompt, "width": int(width), "height": int(height),
                               "length": length, "first_frame": ["lock_img", 0]})
        node["inputs"].pop("last_frame", None)
        wf["noise"]["inputs"]["noise_seed"] = int(seed)
        wf["scheduler"]["inputs"]["steps"] = int(steps)
        wf["save_video"]["inputs"]["filename_prefix"] = prefix
        return wf

    present = [w for w in (shot.get("characters_present") or [])]

    # --- empty shot (no cast member): plain text-to-video -------------------
    # H3CastToVideo needs at least one member, so an establishing shot with no
    # character must go through the i2v node without a first frame.
    if not present:
        node = wf["h3_i2v"]
        node["inputs"].update({"prompt": prompt, "width": int(width),
                               "height": int(height), "length": length})
        node["inputs"].pop("first_frame", None)
        node["inputs"].pop("last_frame", None)
        wf["noise"]["inputs"]["noise_seed"] = int(seed)
        wf["scheduler"]["inputs"]["steps"] = int(steps)
        wf["save_video"]["inputs"]["filename_prefix"] = prefix
        return wf

    wf.pop("h3_i2v", None)
    cast_links = []
    idx = 0
    max_views = 1
    for who in present:
        c = chars.get(who) or chars.get(str(who))
        if not c or idx >= 3:
            continue
        refs = c.get("reference_images") or ([c["reference_image"]] if c.get("reference_image") else [])
        if not refs:
            log(f"WARNING: {who} has no reference image — skipped")
            continue
        idx += 1
        node = f"cast_member_{idx}"
        inputs = {"operation": "Create", "saved_member": "(none -- unused in Create mode)",
                  "name": c.get("name") or str(who),
                  "description": c.get("description") or "", "confirm_update": True}
        used = 0
        for j, ref in enumerate(refs[:4], start=1):
            p = Path(ref)
            if not p.is_absolute():
                p = (REPO / ref).resolve()
            if not p.is_file():
                log(f"WARNING: missing reference {p}")
                continue
            img_node = f"{node}_img{j}"
            wf[img_node] = {"class_type": "LoadImage", "inputs": {"image": stage_image(p)}}
            inputs[f"image_{j}"] = [img_node, 0]
            used += 1
        max_views = max(max_views, used)
        wf[node] = {"class_type": "H3CastMember", "inputs": inputs}
        cast_links.append([node, 0])

    inputs = {"clip": ["load_clip", 0], "vae": ["vae_video", 0], "audio_vae": ["vae_audio", 0],
              "prompt": prompt, "width": int(width), "height": int(height), "length": length,
              "max_views_per_member": max_views, "auto_intro": True, "include_voices": False,
              "ref_image_size": "match"}
    for i, link in enumerate(cast_links, start=1):
        inputs[f"cast_{i}"] = link
    if scene:
        desc = ", ".join(x for x in (scene.get("location"), scene.get("time_of_day"),
                                     scene.get("atmosphere")) if x)
        if desc:
            inputs["scene_description"] = desc
        # scene reference image: this is what keeps every shot in the same place
        imgs = scene.get("images") or ([scene["reference_image"]] if scene.get("reference_image") else [])
        for p in imgs[:1]:
            sp = Path(p)
            if not sp.is_absolute():
                sp = (REPO / p).resolve()
            if sp.is_file():
                wf["scene_img"] = {"class_type": "LoadImage",
                                   "inputs": {"image": stage_image(sp)}}
                inputs["scene_images"] = ["scene_img", 0]
            else:
                log(f"WARNING: missing scene image {sp}")
    wf["cast_to_video"] = {"class_type": "H3CastToVideo", "inputs": inputs}
    wf["guider"]["inputs"]["conditioning"] = ["cast_to_video", 0]
    wf["sampler"]["inputs"]["latent_image"] = ["cast_to_video", 1]
    wf["noise"]["inputs"]["noise_seed"] = int(seed)
    wf["scheduler"]["inputs"]["steps"] = int(steps)
    wf["save_video"]["inputs"]["filename_prefix"] = prefix
    return wf


def run_shot(server: str, wf: dict, timeout: float = 1800) -> tuple[bool, str]:
    try:
        r = post_json(f"{server}/prompt", {"prompt": wf, "client_id": "open-video-drama"})
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
            if st.get("status_str") == "success":
                return True, json.dumps({"prompt_id": pid, "outputs": h[pid].get("outputs", {})})
            return False, f"status={st.get('status_str')}"
        time.sleep(3)
    return False, "timeout"


def download_output(server: str, info: str, dst: Path) -> bool:
    outs = (json.loads(info) or {}).get("outputs", {})
    for payload in outs.values():
        for key in ("gifs", "videos", "images"):
            for g in payload.get(key) or []:
                q = urllib.parse.urlencode({"filename": g["filename"],
                                            "subfolder": g.get("subfolder", ""),
                                            "type": g.get("type", "output")})
                dst.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(f"{server}/view?{q}", timeout=180) as r, dst.open("wb") as fh:
                    shutil.copyfileobj(r, fh)
                if dst.stat().st_size > 1024:
                    return True
    return False


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def restart_engine(port: int, flags: str, wait_s: int = 240) -> bool:
    """Kill whatever listens on `port` and bring a fresh engine up."""
    subprocess.run(["pkill", "-f", f"main.py --listen 127.0.0.1 --port {port}"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    log_path = REPO / ".cache" / f"comfy-restart-{int(time.time())}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(REPO / ".venv/bin/python"), "main.py", "--listen", "127.0.0.1",
           "--port", str(port)] + [f for f in flags.split() if f]
    with log_path.open("ab") as fh:
        subprocess.Popen(cmd, cwd=str(REPO / "ComfyUI"), stdout=fh, stderr=subprocess.STDOUT)
    t0 = time.time()
    while time.time() - t0 < wait_s:
        try:
            get_json(f"http://127.0.0.1:{port}/system_stats", timeout=3)
            log(f"engine restarted after {time.time() - t0:.0f}s (log {log_path.name})")
            return True
        except Exception:  # noqa: BLE001
            time.sleep(2)
    log("engine restart TIMED OUT")
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render drama_script.json to final.mp4")
    ap.add_argument("script")
    ap.add_argument("--out", default=None)
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--width", type=int, default=1376)
    ap.add_argument("--height", type=int, default=768)
    ap.add_argument("--steps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--retries", type=int, default=1, help="retries per shot on failure")
    ap.add_argument("--chain-ssim", type=float, default=0.85,
                    help="minimum SSIM for a [chain] hand-off")
    ap.add_argument("--chain-retries", type=int, default=2)
    ap.add_argument("--upscale", dest="upscale", action="store_true", default=True)
    ap.add_argument("--no-upscale", dest="upscale", action="store_false")
    ap.add_argument("--upscale-server", default="http://127.0.0.1:8288")
    ap.add_argument("--upscale-comfy-dir", default=os.environ.get("OV_COMFYUI_UP_DIR"))
    ap.add_argument("--megapixels", type=float, default=2.0)
    ap.add_argument("--port", type=int, default=8188)
    ap.add_argument("--engine-flags", default="--cache-none")
    ap.add_argument("--restart-every", type=int, default=0,
                    help="recycle the engine every N shots (0 = never; use 3 on this box)")
    ap.add_argument("--only", default=None,
                    help="comma-separated shot ids to render (e.g. s03,s04); "
                         "lets the queue run one shot per task")
    ap.add_argument("--first-frame", default=None,
                    help="pin the first frame of the (single) rendered shot to this image")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    spath = Path(args.script).expanduser().resolve()
    script = json.loads(spath.read_text(encoding="utf-8"))
    base = spath.parent
    pid = script.get("project_id") or "drama"
    out_dir = Path(args.out) if args.out else (REPO / "segments" / pid)
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = out_dir / ".frames"

    chars: dict[str, dict] = {}
    for c in script.get("characters") or []:
        ref = c.get("reference_image")
        entry = dict(c)
        if ref and not Path(ref).is_absolute():
            cand = base / ref
            entry["reference_image"] = str(cand.resolve() if cand.exists() else (REPO / ref).resolve())
        chars[str(c.get("character_id") or c.get("name"))] = entry
        chars[str(c.get("name"))] = entry
    scenes = {str(s.get("scene_id")): s for s in script.get("scenes") or []}
    # enrich each scene with its library card (camera / props / plate image)
    lib_scenes = REPO / "drama" / "library" / "scenes"
    for sid, sc in list(scenes.items()):
        card = lib_scenes / sid / "card.json"
        if card.is_file():
            try:
                extra = json.loads(card.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                extra = {}
            for k in ("camera", "props", "light", "images"):
                if extra.get(k) and not sc.get(k):
                    sc[k] = extra[k]
    style = str(script.get("visual_style") or "")
    shots = script.get("shots") or []
    t_start = time.time()
    log(f"{len(shots)} shot(s) · {args.width}x{args.height} · style={style[:40]}… · out={out_dir}")

    if args.dry_run:
        for sh in shots:
            print(f"\n--- {sh.get('shot_id')} {sh.get('duration_sec')}s [{sh.get('transition')}]")
            print(build_prompt(sh, chars, style, scenes.get(str(sh.get("scene_id")))))
        return 0

    # ensure the engine exists — the renderer owns it start to finish
    try:
        get_json(f"http://127.0.0.1:{args.port}/system_stats", timeout=3)
        log(f"engine already up on :{args.port}")
    except Exception:  # noqa: BLE001
        log(f"no engine on :{args.port} — starting one ({args.engine_flags})")
        if not restart_engine(args.port, args.engine_flags):
            log("could not start the engine")
            return 1

    manifest = out_dir / "manifest.jsonl"
    prev_video: Path | None = None
    idle_gaps: list[float] = []
    last_done_at: float | None = None
    failures: list[str] = []
    only = set(x.strip() for x in args.only.split(",")) if args.only else None
    if only:
        log(f"--only {sorted(only)}: rendering just these shots")
    seen_scenes: set[str] = set()

    for i, sh in enumerate(shots, start=1):
        if only and str(sh.get("shot_id")) not in only:
            continue
        # a chain shot needs the previous shot's last frame even when only this
        # shot is being rendered in the current task
        if prev_video is None and i > 1:
            cand = out_dir / f"{i - 1:04d}.mp4"
            if cand.exists() and cand.stat().st_size > 100 * 1024:
                prev_video = cand
        final = out_dir / f"{i:04d}.mp4"
        if final.exists() and final.stat().st_size > 100 * 1024:
            log(f"[{i:02d}] skip {final.name} (exists)")
            prev_video = final
            continue

        scene = scenes.get(str(sh.get("scene_id")))
        chain = str(sh.get("transition")) == "chain" and prev_video is not None
        lock: Path | None = None
        if args.first_frame and args.only and len(str(args.only).split(",")) == 1:
            pinned = Path(args.first_frame).resolve()
            if pinned.is_file():
                lock = pinned
                log(f"[{i:02d}] first frame pinned to {pinned.name}")
            else:
                log(f"[{i:02d}] pinned first frame missing: {pinned}")
        if lock is None and chain:
            cand = frames / f"{i-1:04d}_last.png"
            if not cand.exists():
                extract_last_frame(prev_video, cand)
            if cand.exists():
                lock = cand

        # ---- scene anchor: use the empty plate as this shot's first frame ----
        # This is the hard version of "same place": the space is pixel-locked
        # instead of merely referenced. Best for the establishing shot of a
        # scene (no people), because i2v carries no cast reference.
        scene_id = str(sh.get("scene_id"))
        first_in_scene = scene_id not in seen_scenes
        seen_scenes.add(scene_id)
        anchored = False
        if lock is None and scene:
            want_anchor = (str(sh.get("anchor") or "") == "plate"
                           or (first_in_scene and not (sh.get("characters_present") or [])
                               and str(sh.get("transition")) != "chain"))
            imgs = scene.get("images") or ([scene["reference_image"]]
                                           if scene.get("reference_image") else [])
            if want_anchor and imgs:
                cand = Path(imgs[0])
                if not cand.is_absolute():
                    cand = (REPO / imgs[0]).resolve()
                if cand.is_file():
                    lock = cand
                    anchored = True
                else:
                    log(f"[{i:02d}] anchor plate missing: {cand}")
        if anchored and (sh.get("characters_present") or []):
            log(f"[{i:02d}] WARNING: anchor=plate 走 i2v，角色参考图不生效——"
                f"建议 anchor 只用于空镜建立镜头")

        if last_done_at is not None:
            gap = round(time.time() - last_done_at, 2)
            idle_gaps.append(gap)
            log(f"[{i:02d}] engine idle gap {gap:.2f}s")

        attempt = 0
        chain_retries = args.chain_retries
        ok = False
        elapsed = 0.0
        peak = 0
        ssim_val: float | None = None
        mode = "i2v-lock" if lock else "cast"
        while attempt <= args.retries:
            attempt += 1
            t0 = time.time()
            wf = build_workflow(sh, chars, style, scene, args.width, args.height,
                                args.steps, args.seed + 13 * (attempt - 1), lock_frame=lock)
            log(f"[{i:02d}] {sh.get('shot_id')} attempt {attempt} ({sh.get('transition')}, {mode})")
            with VramSampler() as sampler:
                good, info = run_shot(args.server, wf)
            peak = max(peak, sampler.peak)

            if not good:
                log(f"[{i:02d}] failed: {info[:200]}")
                if attempt > args.retries:
                    break
                continue

            tmp = out_dir / f".tmp_{i:04d}.mp4"
            if not download_output(args.server, info, tmp):
                log(f"[{i:02d}] download failed")
                continue
            dur = ffprobe_duration(tmp)
            if abs(dur - float(sh.get("duration_sec") or 5)) > 0.75:
                log(f"[{i:02d}] bad duration {dur:.2f}s — retrying")
                tmp.unlink(missing_ok=True)
                continue

            if lock is not None:
                got = frames / f"{i:04d}_first.png"
                if first_frame_of(tmp, got):
                    ssim_val = frame_ssim(lock, got)
                    kind = "anchor" if anchored else "chain"
                    if ssim_val is None:
                        log(f"[{i:02d}] {kind} SSIM unavailable (ffmpeg parse) — gate skipped")
                    else:
                        log(f"[{i:02d}] {kind} SSIM = {ssim_val:.3f} "
                            f"(min {args.chain_ssim:.2f})")
                        # anchor shots are expected to match the plate closely; the
                        # retry gate only makes sense for chain hand-offs
                        if (not anchored and ssim_val < args.chain_ssim
                                and chain_retries > 0):
                            chain_retries -= 1
                            log(f"[{i:02d}] below threshold — retrying with a new seed")
                            tmp.unlink(missing_ok=True)
                            attempt = 0
                            continue

            tmp.replace(final)
            elapsed = time.time() - t0
            ok = True
            break

        entry = {
            "index": i, "shot_id": sh.get("shot_id"), "segment": str(final),
            "status": "ok" if ok else "failed", "mode": mode,
            "anchor": "plate" if anchored else ("chain" if chain else None),
            "attempts": attempt, "elapsed_s": round(elapsed, 1),
            "chain_ssim": None if ssim_val is None else round(ssim_val, 4),
            "peak_vram_mib": peak, "transition": sh.get("transition"),
            "characters_present": sh.get("characters_present"),
            "size_bytes": final.stat().st_size if final.exists() else 0,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if ok:
            prev_video = final
            last_done_at = time.time()
            extract_last_frame(final, frames / f"{i:04d}_last.png")
            log(f"[{i:02d}] OK {elapsed:.1f}s peak={peak} MiB -> {final.name}")
        else:
            failures.append(str(sh.get("shot_id")))
            log(f"[{i:02d}] FAIL after {elapsed:.1f}s")

        if (args.restart_every and ok and i % args.restart_every == 0
                and i < len(shots)):
            log(f"restart-every={args.restart_every}: recycling the engine "
                f"before shot {i + 1}")
            restart_engine(args.port, args.engine_flags)

    if failures:
        log(f"failed shots: {failures} — re-run to retry only those")
        return 1

    # partial runs (--only) stop here: no upscale, no merge
    if only:
        done = len([p for p in out_dir.glob("0*.mp4") if p.stat().st_size > 100 * 1024])
        log(f"--only run finished ({done}/{len(shots)} segments present)")
        return 0

    # ---- optional detail pass ----
    merge_dir = out_dir
    if args.upscale:
        up_dir = out_dir.parent / f"{out_dir.name}_2k"
        log(f"detail pass → {up_dir}")
        up_cmd = [sys.executable, "scripts/upscale_segments.py",
                  "--segments", str(out_dir), "--out", str(up_dir),
                  "--server", args.upscale_server,
                  "--megapixels", str(args.megapixels)]
        if args.upscale_comfy_dir:
            up_cmd += ["--comfy-dir", args.upscale_comfy_dir]
        cp = subprocess.run(up_cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
        print(cp.stdout)
        if cp.returncode != 0:
            log("detail pass FAILED — refusing to merge a half-upscaled film (use --no-upscale)")
            return 1
        merge_dir = up_dir

    if idle_gaps:
        log(f"engine idle gaps {idle_gaps} total={sum(idle_gaps):.1f}s "
            f"({100.0 * sum(idle_gaps) / max(1e-6, time.time() - t_start):.2f}% of wall)")

    # ---- merge ----
    log("merging…")
    out_mp4 = REPO / "output" / f"{pid}.mp4"
    cp = subprocess.run([str(REPO / ".venv/bin/python"), "scripts/merge.py",
                         "--segments", str(merge_dir), "--out", str(out_mp4)],
                        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    print(cp.stdout)
    if cp.returncode != 0:
        log("merge failed")
        return 1
    log(f"final: {out_mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

