"""音乐生成：MiniMax Music 3（本地）。

用法：
    python scripts/gen_music.py --caption "warm acoustic guitar, 1990s rural China" \
        --seconds 60 --seed 7 --out /path/out.flac

工作流（与官方 blueprint 一致）：
    UNETLoader(music dit) + CLIPLoader(music TE, type=minimax) + VAELoader(dav)
    MiniMaxMusic3TextEncode(caption, lyrics, seed, max_duration, cfg_scale, top_k)
    → ConditioningZeroOut（负向）
    → EmptyMiniMaxMusic3LatentAudio(seconds=duration)
    → KSampler(steps, cfg=1.7, euler/simple)
    → VAEDecodeAudio → SaveAudio
"""

from __future__ import annotations

import argparse
import subprocess
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.exec.comfy import ComfyClient  # noqa: E402

try:
    from studio import config as _cfg
    _DEF_SERVER = _cfg.get("comfy.url", "http://127.0.0.1:8288")
    _MODELS = _cfg.load().get("models", {})
except Exception:  # noqa: BLE001
    _DEF_SERVER = "http://127.0.0.1:8288"
    _MODELS = {}

UNET = _MODELS.get("music_unet", "minimax_music3_dit_int8_convrot.safetensors")
CLIP = _MODELS.get("music_text_encoder", "minimax_music3_text_encoder_pruned_int8_convrot.safetensors")
VAE = _MODELS.get("music_vae", "minimax_music3_dav.safetensors")


def log(msg: str) -> None:
    print(f"[music {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def build_workflow(caption: str, lyrics: str, seconds: float, seed: int,
                   steps: int, cfg: float, top_k: int) -> dict:
    return {
        "unet": {"class_type": "UNETLoader",
                 "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "clip": {"class_type": "CLIPLoader",
                 "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "te": {"class_type": "MiniMaxMusic3TextEncode",
               "inputs": {"clip": ["clip", 0], "caption": caption, "lyrics": lyrics,
                          "seed": seed, "max_duration": float(seconds),
                          "cfg_scale": cfg, "top_k": top_k}},
        "neg": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["te", 0]}},
        "latent": {"class_type": "EmptyMiniMaxMusic3LatentAudio",
                   "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "sampler": {"class_type": "KSampler",
                    "inputs": {"model": ["unet", 0], "seed": seed, "steps": steps,
                               "cfg": cfg, "sampler_name": "euler", "scheduler": "simple",
                               "positive": ["te", 0], "negative": ["neg", 0],
                               "latent_image": ["latent", 0], "denoise": 1.0}},
        "decode": {"class_type": "VAEDecodeAudio",
                   "inputs": {"samples": ["sampler", 0], "vae": ["vae", 0]}},
        "save": {"class_type": "SaveAudio",
                 "inputs": {"audio": ["decode", 0], "filename_prefix": "ov_music"}},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="MiniMax Music 3 本地生成")
    ap.add_argument("--caption", required=True, help="风格/乐器/情绪的英文描述")
    ap.add_argument("--lyrics", default="", help="歌词；留空=纯器乐")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--cfg", type=float, default=1.7)
    ap.add_argument("--top-k", type=int, default=50)
    ap.add_argument("--out", required=True)
    ap.add_argument("--segments", type=int, default=1,
                    help="分几段生成（>35 秒建议 2 段，段间交叉淡化）")
    ap.add_argument("--crossfade", type=float, default=2.0, help="段间交叉淡化秒数")
    ap.add_argument("--server", default=_DEF_SERVER)
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    wf = build_workflow(args.caption, args.lyrics, args.seconds, args.seed,
                        args.steps, args.cfg, args.top_k)
    if args.dry_run:
        print(json.dumps({"nodes": sorted(wf), "seconds": args.seconds}, ensure_ascii=False))
        return 0

    client = ComfyClient(args.server)
    try:
        client.post("/free", {"unload_models": True, "free_memory": True})
        time.sleep(1.5)
    except Exception:  # noqa: BLE001
        pass

    dst = Path(args.out).expanduser().resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    segs = max(1, min(4, args.segments))
    seg_seconds = round(args.seconds / segs, 2)
    parts: list[Path] = []
    t0 = time.time()
    for i in range(segs):
        wf = build_workflow(args.caption, args.lyrics, seg_seconds, args.seed + i * 17,
                            args.steps, args.cfg, args.top_k)
        ok, pid = client.submit(wf)
        if not ok:
            log(f"第 {i + 1}/{segs} 段提交失败：{pid}")
            return 1
        log(f"第 {i + 1}/{segs} 段已提交 {pid} {seg_seconds:.0f}s seed={args.seed + i * 17}")
        ok, info = client.wait(pid, args.timeout, log=log)
        if not ok:
            log(f"第 {i + 1} 段失败：{info}")
            return 1
        part = dst.with_name(f"{dst.stem}_p{i}{dst.suffix}")
        if not client.download(info, part, keys=("audio", "audios", "gifs", "videos", "images")):
            log(f"第 {i + 1} 段下载失败")
            return 1
        parts.append(part)

    if len(parts) == 1:
        parts[0].replace(dst)
    else:
        # 交叉淡化拼接，末尾 1.2 秒淡出（盖掉可能出现的尾巴噪声）
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for part in parts:
            cmd += ["-i", str(part)]
        filt = []
        prev = "0:a"
        for i in range(1, len(parts)):
            out = f"x{i}"
            filt.append(f"[{prev}][{i}:a]acrossfade=d={args.crossfade}:c1=tri:c2=tri[{out}]")
            prev = out
        dur = float(args.seconds)
        filt.append(f"[{prev}]afade=t=out:st={max(0.0, dur - 1.2):.2f}:d=1.2[out]")
        cmd += ["-filter_complex", ";".join(filt), "-map", "[out]", str(dst)]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        if cp.returncode != 0 or not dst.is_file():
            log(f"拼接失败：{(cp.stderr or '')[-300:]}")
            return 1
        for part in parts:
            part.unlink(missing_ok=True)

    log(f"完成 {dst} {dst.stat().st_size // 1024} KB · 用时 {time.time() - t0:.0f}s")
    print(json.dumps({"ok": True, "out": str(dst), "seconds": args.seconds,
                      "seed": args.seed, "elapsed_s": round(time.time() - t0, 1)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
