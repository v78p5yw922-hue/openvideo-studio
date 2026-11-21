"""统一配置加载：默认值 < config.toml < 环境变量。

用法：
    from studio import config
    config.get("comfy.url")            # 带默认值
    config.get("paths.data_dir", "")   # 取不到给空

配置文件查找顺序：
    1. $OPEN_VIDEO_CONFIG 指定的路径
    2. <仓库根>/config.toml
    3. <数据目录>/config.toml
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

try:                      # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # Python 3.10
    try:
        import tomli as _toml  # type: ignore
    except ModuleNotFoundError:
        _toml = None


def _parse_toml_fallback(text: str) -> dict:
    """极简 TOML 解析：只支持本项目用到的 section / key = 字符串|整数|浮点|布尔|数组。"""
    root: dict = {}
    cur = root
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            cur = root
            for part in name.split("."):
                cur = cur.setdefault(part.strip(), {})
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if val.startswith('"') and val.endswith('"'):
            cur[key] = val[1:-1]
        elif val.startswith("[") and val.endswith("]"):
            items = [x.strip() for x in val[1:-1].split(",") if x.strip()]
            out = []
            for it in items:
                try:
                    out.append(float(it))
                except ValueError:
                    out.append(it.strip('"'))
            cur[key] = out
        elif val.lower() in ("true", "false"):
            cur[key] = val.lower() == "true"
        else:
            try:
                cur[key] = int(val)
            except ValueError:
                try:
                    cur[key] = float(val)
                except ValueError:
                    cur[key] = val
    return root


def _read_toml(path: Path) -> dict:
    if _toml is not None:
        with path.open("rb") as fh:
            return _toml.load(fh)
    return _parse_toml_fallback(path.read_text(encoding="utf-8"))

_REPO = Path(__file__).resolve().parents[1]

DEFAULTS: dict[str, Any] = {
    "comfy": {
        "url": "http://127.0.0.1:8288",
        "root": "/home/agent/openvideo/ComfyUI-up",
        "timeout": 300,
    },
    "paths": {
        # 空 = 用下面的内置默认（保持与旧版一致）
        "data_dir": "",
        "content_dir": "",
    },
    "models": {
        "video_unet": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "ref_unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "image_unet": "minimax_h3_image_pruned_int8_convrot.safetensors",
        "text_encoder": "qwen3vl_32b_minimax_h3_int8_convrot.safetensors",
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
        "image_vae": "minimax_h3_image_vae_fp16.safetensors",
    },
    "render": {
        "width": 1344,
        "height": 768,
        "steps": 20,
        "durations": [5.0, 8.0, 10.0],
        "max_wall": 0,          # 0 = 不限（按镜时长的 shot_timeout 兜底）
    },
    "gpu": {
        "policy": "queue",      # queue | render_priority
    },
}

# 环境变量映射：环境变量永远优先于 toml（容器部署时最方便）
ENV_MAP = {
    "comfy.url": ("OPEN_VIDEO_COMFYUI", str),
    "comfy.root": ("OPEN_VIDEO_COMFY_ROOT", str),
    "paths.data_dir": ("STUDIO_DATA", str),
    "paths.content_dir": ("OPEN_VIDEO_CONTENT_DIR", str),
}

_cache: dict[str, Any] | None = None
_loaded_from: Path | None = None


def _candidates() -> list[Path]:
    out = []
    env = os.environ.get("OPEN_VIDEO_CONFIG", "").strip()
    if env:
        out.append(Path(env))
    out.append(_REPO / "config.toml")
    data = os.environ.get("STUDIO_DATA", "").strip()
    if data:
        out.append(Path(data) / "config.toml")
    return out


def _merge(dst: dict, src: dict) -> dict:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _merge(dst[k], v)
        else:
            dst[k] = v
    return dst


def load(force: bool = False) -> dict[str, Any]:
    global _cache, _loaded_from
    if _cache is not None and not force:
        return _cache
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for path in _candidates():
        try:
            if path.is_file():
                _merge(cfg, _read_toml(path))
                _loaded_from = path
                break
        except Exception:  # noqa: BLE001  坏配置不应让服务起不来
            continue
    for dotted, (env_name, caster) in ENV_MAP.items():
        raw = os.environ.get(env_name, "").strip()
        if raw:
            node = cfg
            parts = dotted.split(".")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = caster(raw)
    _cache = cfg
    return cfg


def get(dotted: str, default: Any = None) -> Any:
    node: Any = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def data_dir() -> Path:
    raw = str(get("paths.data_dir", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "openvideo" / "studio-data"


def content_dir() -> Path:
    """剧本/分镜等创作文件所在的目录（不属于代码仓库）。"""
    raw = str(get("paths.content_dir", "") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _REPO / "content"


def config_file() -> Path | None:
    load()
    return _loaded_from
