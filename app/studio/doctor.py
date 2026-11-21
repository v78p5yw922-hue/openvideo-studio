"""环境体检（doctor）：不渲任何东西，只回答"现在能不能渲、缺什么"。

用途：
    python -m studio.doctor            # 人看的表格，有 fail 时退出码 1
    python -m studio.doctor --json     # 给 UI / 脚本用
    GET /api/doctor                    # 同一个报告

检查项：
  1. 配置文件是否命中
  2. ComfyUI 是否在线（/system_stats）
  3. 所需节点是否齐全（/object_info）
  4. 权重文件是否齐（按 config.models.* 的名字）
  5. ffmpeg / ffprobe 是否可用
  6. 数据目录是否可写、剩余空间
  7. GPU 能不能读到（有 nvidia-smi 时）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from . import config

# 渲染必需的自定义节点（cast 分支）与原生节点
REQUIRED_NODES = {
    "UNETLoader": "原生",
    "CLIPLoader": "原生",
    "VAELoader": "原生",
    "LoadImage": "原生",
    "KSamplerSelect": "原生",
    "BasicScheduler": "原生",
    "RandomNoise": "原生",
    "SamplerCustomAdvanced": "原生",
    "VAEDecode": "原生",
    "SaveImage": "原生",
    "EmptyLatentImage": "原生",
    "H3CastMember": "comfy-kitchen",
    "H3CastToVideo": "comfy-kitchen",
    "MiniMaxH3AddGuide": "ComfyUI ≥ 0.45.3",
    "MiniMaxH3ImageConditioning": "ComfyUI ≥ 0.45.3",
}

MODEL_DIRS = {
    "video_unet": "diffusion_models",
    "ref_unet": "diffusion_models",
    "image_unet": "diffusion_models",
    "text_encoder": "text_encoders",
    "video_vae": "vae",
    "audio_vae": "vae",
    "image_vae": "vae",
}


def _http_json(url: str, timeout: float = 5.0):
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def _check(key: str, label: str, status: str, detail: str = "", hint: str = "") -> dict:
    return {"key": key, "label": label, "status": status, "detail": detail, "hint": hint}


def check_config() -> dict:
    f = config.config_file()
    if f:
        return _check("config", "配置文件", "ok", str(f))
    return _check("config", "配置文件", "warn",
                  "没有 config.toml，使用内置默认值",
                  "复制 config.example.toml 成 config.toml 再改")


def check_engine() -> tuple[dict, dict | None]:
    url = str(config.get("comfy.url", "http://127.0.0.1:8288")).rstrip("/")
    try:
        stats = _http_json(f"{url}/system_stats", timeout=5)
        dev = ""
        try:
            d = (stats.get("devices") or [{}])[0]
            dev = f"{d.get('name', '')} {round(int(d.get('vram_total', 0)) / 2**30, 1)}GB"
        except Exception:  # noqa: BLE001
            pass
        return _check("engine", "ComfyUI 在线", "ok", f"{url} {dev}".strip()), stats
    except Exception as e:  # noqa: BLE001
        return _check("engine", "ComfyUI 在线", "fail",
                      f"{url} 连不上：{str(e)[:80]}",
                      "检查端口、是否启动 comfyui 服务、config.toml 的 [comfy].url"), None


def check_nodes() -> dict:
    url = str(config.get("comfy.url", "http://127.0.0.1:8288")).rstrip("/")
    try:
        info = _http_json(f"{url}/object_info", timeout=20)
    except Exception as e:  # noqa: BLE001
        return _check("nodes", "节点齐全", "fail", f"读不到 object_info：{str(e)[:60]}",
                      "先让 ComfyUI 起来")
    missing = [f"{n}（{why}）" for n, why in REQUIRED_NODES.items() if n not in info]
    if missing:
        return _check("nodes", "节点齐全", "fail",
                      "缺 " + "、".join(missing[:6]) + ("…" if len(missing) > 6 else ""),
                      "装 comfy-kitchen 0.2.41，并把 ComfyUI 升到 ≥0.45.3")
    return _check("nodes", "节点齐全", "ok", f"{len(REQUIRED_NODES)} 个必需节点都在")


def check_models() -> dict:
    root = Path(str(config.get("comfy.root", ""))).expanduser()
    if not str(root) or not root.is_dir():
        return _check("models", "权重文件", "fail", f"ComfyUI 目录不存在：{root}",
                      "config.toml 的 [comfy].root 指向 ComfyUI 安装目录")
    models_dir = root / "models"
    missing, found = [], []
    for key, sub in MODEL_DIRS.items():
        name = str(config.get(f"models.{key}", ""))
        if not name:
            continue
        p = models_dir / sub / name
        if p.is_file() and p.stat().st_size > 1 << 20:
            found.append(f"{key}({p.stat().st_size / 2**30:.1f}G)")
        else:
            missing.append(f"{key}: {name}")
    if missing:
        return _check("models", "权重文件", "fail",
                      "缺 " + "；".join(missing[:3]) + (f" 等 {len(missing)} 个" if len(missing) > 3 else ""),
                      "按 docs/MODELS.md 下载；文件名要与 config.toml 的 [models] 一致")
    return _check("models", "权重文件", "ok", "、".join(found))


def check_ffmpeg() -> dict:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            return _check("ffmpeg", "ffmpeg 可用", "fail", f"找不到 {tool}",
                          "apt install ffmpeg（WSL/Ubuntu）或 choco install ffmpeg（Windows）")
    out = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
    ver = (out.stdout or "").splitlines()[0][:40] if out.stdout else ""
    return _check("ffmpeg", "ffmpeg 可用", "ok", ver)


def check_storage() -> dict:
    data = config.data_dir()
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except Exception as e:  # noqa: BLE001
        return _check("storage", "数据目录可写", "fail", f"{data}：{str(e)[:60]}",
                      "换成有写权限的目录（config.toml 的 [paths].data_dir）")
    free_gb = shutil.disk_usage(str(data)).free / 2**30
    status = "ok" if free_gb > 20 else ("warn" if free_gb > 5 else "fail")
    hint = "" if status == "ok" else "一集成片约 1–3 GB，建议留 20 GB 以上"
    return _check("storage", "数据目录可写", status,
                  f"{data} · 剩余 {free_gb:.0f} GB", hint)


def check_gpu() -> dict:
    for exe in ("nvidia-smi", "/usr/lib/wsl/lib/nvidia-smi",
                "/mnt/c/Windows/System32/nvidia-smi.exe"):
        if shutil.which(exe) or Path(exe).is_file():
            try:
                out = subprocess.run(
                    [exe, "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=15)
                line = (out.stdout or "").strip().splitlines()
                if line:
                    return _check("gpu", "GPU 可见", "ok", line[0])
            except Exception:  # noqa: BLE001
                continue
    return _check("gpu", "GPU 可见", "warn", "读不到 nvidia-smi",
                  "WSL 里没有 nvidia-smi 不影响渲染，只是这里看不到显存")


def report() -> dict:
    engine, _stats = check_engine()
    checks = [
        check_config(),
        engine,
        check_nodes(),
        check_models(),
        check_ffmpeg(),
        check_storage(),
        check_gpu(),
    ]
    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    return {
        "ok": not fails,
        "summary": ("可以开始" if not fails
                    else f"{len(fails)} 项必须修复"),
        "fail": len(fails),
        "warn": len(warns),
        "checks": checks,
        "config_file": str(config.config_file() or ""),
        "data_dir": str(config.data_dir()),
        "content_dir": str(config.content_dir()),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="检查这台机器能不能跑 OpenVideo Studio")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep = report()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0 if rep["ok"] else 1

    icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
    print(f"\nOpenVideo Studio · 环境体检\n{'─' * 60}")
    for c in rep["checks"]:
        print(f"{icon[c['status']]} {c['label']:<12} {c['detail']}")
        if c["hint"] and c["status"] != "ok":
            print(f"   ↳ {c['hint']}")
    print(f"{'─' * 60}")
    print(f"数据目录: {rep['data_dir']}")
    print(f"内容目录: {rep['content_dir']}")
    print(f"结论：{rep['summary']}\n")
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
