#!/usr/bin/env python3
"""H3-Image 文生图：提交 ComfyUI 工作流，等结果，下载 PNG。

    python scripts/gen_image.py --prompt "..." --out /path/x.png \
        --width 2048 --height 2048 --steps 6 --seed 42

节点图（引擎 0.45.3 本地节点）：
    UNETLoader(minimax_h3_image) + CLIPLoader(qwen3vl, type=minimax) + VAELoader(image vae)
    -> MiniMaxH3ImageConditioning(clip, prompt, width, height)
    -> BasicGuider + KSamplerSelect(euler) + BasicScheduler(simple, denoise=1)
    -> RandomNoise + SamplerCustomAdvanced -> VAEDecode -> SaveImage

最后一行输出 JSON：{"ok": true, "out": "...", "seed": 42, "elapsed_s": 12.3}
"""

from __future__ import annotations

try:
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from studio import config as _cfg
    _CFG = _cfg.load()
except Exception:  # noqa: BLE001
    _CFG = {}


def _cfgj(path: str, default):
    node = _CFG
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


import argparse
import json
import os
import random
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMFY_ROOT = Path(os.environ.get("OPEN_VIDEO_COMFY_ROOT") or (REPO / "ComfyUI"))
DEFAULT_SERVER = os.environ.get("OPEN_VIDEO_COMFYUI", _cfgj("comfy.url", "http://127.0.0.1:8288"))

UNET_FILE = _cfgj("models.image_unet", "minimax_h3_image_pruned_int8_convrot.safetensors")
CLIP_FILE = _cfgj("models.text_encoder", "qwen3vl_32b_minimax_h3_int8_convrot.safetensors")
VAE_FILE = _cfgj("models.image_vae", "minimax_h3_image_vae_fp16.safetensors")


def log(msg: str) -> None:
    print(f"[gen {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def post_json(url: str, payload: dict, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url: str, timeout: float = 15) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def free_vram(server: str, wait: float = 1.5) -> None:
    try:
        post_json(f"{server}/free", {"unload_models": True, "free_memory": True})
        time.sleep(wait)
    except Exception:  # noqa: BLE001  清理失败不该阻断生成
        pass


def build_workflow(prompt: str, width: int, height: int, steps: int,
                   seed: int, denoise: float, refs: list[str] | None = None) -> dict:
    prefix = f"ov_asset_{time.strftime('%Y%m%d_%H%M%S')}"
    cond_inputs = {
        "clip": ["clip", 0],
        "prompt": prompt,
        "width": int(width),
        "height": int(height),
        "vae": ["vae", 0],
    }
    wf: dict = {}
    # 参考图：image_1 是"被编辑的那张"，其余是构图参考（autogrow 以嵌套 dict 传）
    if refs:
        cond_inputs["images"] = {}
    for i, name in enumerate(refs or [], 1):
        wf[f"ref{i}"] = {"class_type": "LoadImage", "inputs": {"image": name}}
        cond_inputs["images"][f"image_{i}"] = [f"ref{i}", 0]

    wf.update({
        "unet": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET_FILE, "weight_dtype": "default"},
        },
        "clip": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP_FILE, "type": "minimax"},
        },
        "vae": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VAE_FILE},
        },
        "cond": {
            "class_type": "MiniMaxH3ImageConditioning",
            "inputs": cond_inputs,
        },
        "guider": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["unet", 0], "conditioning": ["cond", 0]},
        },
        "noise": {
            "class_type": "RandomNoise",
            "inputs": {"noise_seed": int(seed)},
        },
        "sampler": {
            "class_type": "KSamplerSelect",
            "inputs": {"sampler_name": "euler"},
        },
        "sched": {
            "class_type": "BasicScheduler",
            "inputs": {
                "model": ["unet", 0],
                "scheduler": "simple",
                "steps": int(steps),
                "denoise": float(denoise),
            },
        },
        "sample": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["noise", 0],
                "guider": ["guider", 0],
                "sampler": ["sampler", 0],
                "sigmas": ["sched", 0],
                "latent_image": ["cond", 1],
            },
        },
        "decode": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["sample", 0], "vae": ["vae", 0]},
        },
        "save": {
            "class_type": "SaveImage",
            "inputs": {"images": ["decode", 0], "filename_prefix": prefix},
        },
    })
    return wf


def download_image(server: str, outputs: dict, dst: Path) -> bool:
    for payload in outputs.values():
        for g in payload.get("images") or []:
            q = urllib.parse.urlencode({"filename": g["filename"],
                                        "subfolder": g.get("subfolder", ""),
                                        "type": g.get("type", "output")})
            dst.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(f"{server}/view?{q}", timeout=180) as r, dst.open("wb") as fh:
                fh.write(r.read())
            if dst.stat().st_size > 1024:
                return True
    return False


def drop_refs(names: list[str]) -> None:
    """跑完把临时参考图从 ComfyUI/input 删掉，别越积越多。"""
    for name in names:
        try:
            (COMFY_ROOT / "input" / name).unlink()
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width", type=int, default=2048)
    ap.add_argument("--height", type=int, default=2048)
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--seed", type=int, default=-1, help="-1 = 随机")
    ap.add_argument("--denoise", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--server", default=DEFAULT_SERVER)
    ap.add_argument("--no-free", action="store_true",
                    help="上一张也是文生图时跳过显存清理，复用已加载的权重")
    ap.add_argument("--ref", action="append", default=[],
                    help="参考图本地路径，可重复；第 1 张是 image_1（被编辑的那张）")
    args = ap.parse_args()

    prompt = (args.prompt or "").strip()
    if not prompt:
        print("prompt 不能为空", file=sys.stderr)
        return 2

    # 参考图拷进 ComfyUI/input（LoadImage 只认这个目录里的文件）
    ref_names: list[str] = []
    for p in args.ref:
        src = Path(p).expanduser()
        if not src.is_file():
            print(f"参考图不存在: {src}", file=sys.stderr)
            return 2
        ext = src.suffix.lower() or ".png"
        name = f"ov_ref_{uuid.uuid4().hex[:10]}{ext}"
        shutil.copyfile(src, COMFY_ROOT / "input" / name)
        ref_names.append(name)
    if ref_names:
        log(f"已放入 {len(ref_names)} 张参考图")

    seed = args.seed if args.seed >= 0 else random.randint(0, 2**31 - 1)
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    if not args.no_free:
        log("清理显存（切换模型）")
        free_vram(args.server)

    wf = build_workflow(prompt, args.width, args.height, args.steps, seed,
                        args.denoise, ref_names)
    try:
        r = post_json(f"{args.server}/prompt",
                      {"prompt": wf, "client_id": "open-video-asset"})
    except Exception as e:  # noqa: BLE001
        print(f"submit failed: {e}", file=sys.stderr)
        return 3
    if r.get("node_errors"):
        print(f"rejected: {str(r['node_errors'])[:600]}", file=sys.stderr)
        return 4
    pid = r["prompt_id"]
    log(f"已提交 seed={seed} {args.width}x{args.height} steps={args.steps}")

    t0 = time.time()
    last_beat = 0.0
    outputs: dict | None = None
    while time.time() - t0 < args.timeout:
        if time.time() - last_beat >= 20:
            last_beat = time.time()
            log(f"引擎在跑，已等 {int(last_beat - t0)} 秒")
        try:
            h = get_json(f"{args.server}/history/{pid}")
        except Exception:  # noqa: BLE001
            h = {}
        if pid in h:
            st = h[pid].get("status", {})
            if st.get("status_str") != "success":
                drop_refs(ref_names)
                print(f"status={st.get('status_str')}", file=sys.stderr)
                return 5
            outputs = h[pid].get("outputs", {})
            break
        time.sleep(3)

    if outputs is None:
        print("timeout", file=sys.stderr)
        return 6

    if not download_image(args.server, outputs, out):
        print("download failed", file=sys.stderr)
        return 7

    drop_refs(ref_names)
    print(json.dumps({"ok": True, "out": str(out), "seed": seed,
                      "elapsed_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
