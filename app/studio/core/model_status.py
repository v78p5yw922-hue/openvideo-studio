"""文生图模型文件状态 —— 只读扫描 ComfyUI 的 models 目录，不下载、不锁文件。

用户可能在外部（浏览器/aria2）下载，所以我们只报告：
- final 文件存在 → ready
- 只有 .part → downloading（按预期大小算百分比）
- 都没有 → missing
"""

from __future__ import annotations

from pathlib import Path

from .. import config as _config

REPO = Path(__file__).resolve().parents[2]
COMFY_MODELS = Path(str(_config.get("comfy.root", str(REPO / "ComfyUI")))) / "models"

# 文件名以实际下载到的为准；预期大小用于算百分比（plan 里的 12.5G / 1.2G）
MODEL_FILES = [
    {
        "key": "h3_image_unet",
        "label": "文生图 UNET",
        "subdir": "diffusion_models",
        "file": _config.get("models.image_unet", "minimax_h3_image_pruned_int8_convrot.safetensors"),
        "expected": 13_421_772_800,
    },
    {
        "key": "h3_image_vae",
        "label": "图像 VAE",
        "subdir": "vae",
        "file": _config.get("models.image_vae", "minimax_h3_image_vae_fp16.safetensors"),
        "expected": 1_168_505_312,
    },
]


def _size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def status() -> list[dict]:
    out: list[dict] = []
    for m in MODEL_FILES:
        final = COMFY_MODELS / m["subdir"] / m["file"]
        part = final.with_suffix(final.suffix + ".part")
        final_bytes = _size(final)
        part_bytes = _size(part)
        expected = int(m["expected"])

        if final_bytes > 0:
            state, disk = "ready", final_bytes
        elif part_bytes > 0:
            state, disk = "downloading", part_bytes
        else:
            state, disk = "missing", 0

        pct = 0.0
        if expected > 0 and disk > 0:
            pct = min(99.0 if state == "downloading" else 100.0, disk * 100.0 / expected)

        out.append({
            "key": m["key"],
            "file": m["label"],
            "filename": m["file"],
            "subdir": m["subdir"],
            "exists": state == "ready",
            "bytes": expected,
            "bytes_on_disk": disk,
            "state": state,
            "pct": round(pct, 1),
            "speed_mbps": 0,
            "eta_s": 0,
            "error": "",
        })
    return out


def all_ready() -> bool:
    return all(s["state"] == "ready" for s in status())
