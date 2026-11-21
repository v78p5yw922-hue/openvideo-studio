"""Job executor.

Deliberately dumb and deliberately off by default:

* It only picks up `queued` jobs.
* It refuses to start unless `STUDIO_ALLOW_RENDER=1`. The human confirms a plan
  in the UI first; this env var is the second, explicit switch.
* One job at a time — 24 GB VRAM is already saturated by a single 5 s shot.

Commands are overridable so the whole loop can be tested without a GPU:

    STUDIO_RENDER_CMD=/path/to/fake_render.py
    STUDIO_STILL_CMD=/path/to/fake_still.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
import sys
import threading
import time
from pathlib import Path
import uuid

from ..core import assets, db, service
from . import render_adapter

def _app_dir() -> Path:
    """Where the code lives. Works for a git clone at any path."""
    env = os.environ.get("OV_APP_DIR")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2]      # app/


APP_DIR = _app_dir()
REPO = APP_DIR


def _venv_python() -> Path:
    env = os.environ.get("OV_VENV_PY")
    if env:
        return Path(env).expanduser()
    root = APP_DIR.parent / ".venv"
    for rel in ("bin/python", "Scripts/python.exe"):
        if (root / rel).is_file():
            return root / rel
    return Path(sys.executable)


VENV_PY = _venv_python()
POLL_S = 5.0
_thread: threading.Thread | None = None

PEAK_RE = re.compile(r"peak=(\d+)\s*MiB")
SHOT_OK_RE = re.compile(r"\[(\d+)\]\s+OK\s+([0-9.]+)s")
STEP_RE = re.compile(r"(?:step\s+)?(\d+)\s*/\s*(\d+)\b", re.IGNORECASE)
PCT_RE = re.compile(r"(\d{1,3})\s*%")

# live progress: {job_id: {"shot_id": ..., "pct": int, "detail": str, "started": ts}}
PROGRESS: dict[str, dict] = {}
# running subprocesses, so a cancel can actually stop the render
RUNNING: dict[str, subprocess.Popen] = {}




# 引擎端口：改到 8288 是为了避开浏览器（ComfyUI 原生前端）误操作 8188 的
# 队列删除 / 中断请求 —— 那些操作会直接把正在渲染的 prompt 干掉。
_ENGINE_PORT = int(os.environ.get("OPEN_VIDEO_COMFY_PORT", "8288"))


def _engine_alive(port: int = _ENGINE_PORT, timeout: float = 3.0) -> bool:
    """引擎进程在响应就够 —— 忙（在采样）也算活。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system_stats",
                                    timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_engine(port: int = _ENGINE_PORT, timeout_s: int = 45) -> bool:
    """提交前确认引擎可用；真挂了才去拉起，绝不长时间阻塞队列。

    之前这里是"每 5 秒一次 systemctl start，共 24 轮"：
    引擎在高负载下 systemctl 会阻塞几秒到几十秒，一轮能拖 30 秒，
    24 轮 = 十几分钟，单线程执行器整个卡住，排队任务全部静止。
    现在：先判断进程是否响应（忙也算活），只在 unit 确实不是 active 时才 start，
    总等待上限 45 秒，超时就放弃（提交失败会走重试，不会把队列钉死）。
    """
    deadline = time.time() + max(5, timeout_s)
    while time.time() < deadline:
        if _engine_alive(port):
            return True
        try:
            active = subprocess.run(["systemctl", "is-active", "comfyui-8188.service"],
                                    capture_output=True, text=True, timeout=5).stdout.strip()
        except Exception:  # noqa: BLE001
            active = ""
        if active != "active":
            print("[executor] 引擎不在运行，尝试拉起", flush=True)
            try:
                subprocess.run(["systemctl", "start", "comfyui-8188.service"],
                               capture_output=True, timeout=20, check=False)
            except Exception:  # noqa: BLE001
                pass
            time.sleep(3)
        else:
            # 进程在但不响应（正在加载/采样），给它几秒，不要重复 start
            time.sleep(3)
    print("[executor] 引擎未响应，先提交任务（失败会自动重试）", flush=True)
    return False


def kill_orphan_renderers() -> int:
    """杀掉不属于任何在跑任务的 render_drama 进程。

    systemd 若配成 KillMode=process，重启后子进程会被 init 收养、继续占 GPU，
    渲出来的帧却没人收集 —— 表现为"新任务在排队、旧进程在空转"。
    """
    killed = 0
    try:
        out = subprocess.run(["pgrep", "-f", "scripts/(render_drama|gen_image)\\.py"],
                             capture_output=True, text=True).stdout.split()
    except Exception:  # noqa: BLE001
        return 0
    live_pids = {p.pid for p in RUNNING.values() if p}
    for pid in out:
        try:
            ipid = int(pid)
        except ValueError:
            continue
        if ipid in live_pids:
            continue
        try:
            subprocess.run(["kill", "-9", str(ipid)], check=False)
            killed += 1
        except Exception:  # noqa: BLE001
            pass
    if killed:
        print(f"[executor] 清理了 {killed} 个孤儿渲染进程")
    return killed


def kill(job_id: str) -> bool:
    """Terminate the renderer for a job. Returns True if something was killed."""
    # 记录调用来源：如果 kill 被意外触发，日志里能看出是哪条路径
    try:
        import traceback as _tb
        stack = [f"{f.name}:{f.lineno}" for f in _tb.extract_stack()[-5:-1]]
        print(f"[executor] kill({job_id}) 调用链: {' <- '.join(reversed(stack))}", flush=True)
    except Exception:  # noqa: BLE001
        pass
    proc = RUNNING.get(job_id)
    if not proc:
        return False
    try:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:  # noqa: BLE001
        return False
    # 关键：前端 render_drama 已经死了，但引擎里的 prompt 还在跑 —— 必须显式中断
    print(f"[executor] kill({job_id}) 已杀进程，准备中断引擎 prompt", flush=True)
    interrupt()
    free_vram()
    return True


OOM_RE = re.compile(r"out of memory|OutOfMemory|exceed allowed memory", re.IGNORECASE)



def interrupt(port: int = _ENGINE_PORT) -> bool:
    """让 ComfyUI 停掉当前正在跑的那个 prompt。

    只杀掉 render_drama 是不够的：引擎里的 prompt 会继续渲完（8 分钟 GPU）。
    之后重新排队就会变成"同一镜两个 prompt"，看起来像在无限重复渲染。
    """
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/interrupt",
                                     data=b"{}",
                                     headers={"content-type": "application/json",
                                              "x-ov-control": "1"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def free_vram(port: int = _ENGINE_PORT) -> bool:
    """Ask ComfyUI to unload models and release VRAM (used after an OOM)."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/free",
            data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _is_oom(output: str) -> bool:
    return bool(OOM_RE.search(output or ""))


def parse_progress(line: str) -> int | None:
    """Extract a percentage from a renderer/comfyui progress line."""
    if not line:
        return None
    m = PCT_RE.search(line)
    if m:
        return max(0, min(100, int(m.group(1))))
    m = STEP_RE.search(line)
    if m:
        cur, total = int(m.group(1)), int(m.group(2))
        if total > 0:
            return max(0, min(100, round(cur * 100 / total)))
    return None


def progress_of(job_id: str) -> dict | None:
    p = PROGRESS.get(job_id)
    return dict(p) if p else None


def enabled() -> bool:
    return os.environ.get("STUDIO_ALLOW_RENDER", "0") == "1"


def render_cmd() -> list[str]:
    custom = os.environ.get("STUDIO_RENDER_CMD")
    if custom:
        return [custom]
    return [str(VENV_PY), "scripts/render_drama.py"]


def still_cmd() -> list[str]:
    custom = os.environ.get("STUDIO_STILL_CMD")
    if custom:
        return [custom]
    return [str(VENV_PY), "scripts/render_still.py"]


def asset_cmd() -> list[str]:
    custom = os.environ.get("STUDIO_ASSET_CMD")
    if custom:
        return [custom]
    return [str(VENV_PY), "scripts/gen_image.py"]


def start() -> None:
    global _thread
    if os.environ.get("STUDIO_DISABLE_EXECUTOR") == "1":
        return
    if _thread and _thread.is_alive():
        return
    _thread = threading.Thread(target=_loop, name="studio-executor", daemon=True)
    _thread.start()


def _loop() -> None:
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            print(f"[executor] {e}")
        time.sleep(POLL_S)


def tick() -> dict | None:
    """Process at most one queued job. Returns the finished job dict or None."""
    if not enabled():
        return None
    with db.txn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        # atomic claim: only the first executor wins, so two server processes
        # (e.g. after a restart) cannot race on the same queue
        cur = conn.execute(
            """UPDATE jobs SET state='running', started_at=?
               WHERE id=? AND state='queued'
                 AND NOT EXISTS (SELECT 1 FROM jobs WHERE state='running')""",
            (db.now(), row["id"]))
        if cur.rowcount != 1:
            return None
        job = dict(conn.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
    if job["kind"] == "render":
        return _run_render(job, already_started=True)
    if job["kind"] == "preview":
        return _run_preview(job, already_started=True)
    if job["kind"] == "asset":
        return _run_asset(job, already_started=True)
    _finish(job["id"], "failed", f"未知任务类型 {job['kind']}")
    return None


# ------------------------------------------------------------------- render

def _run_render(job: dict, already_started: bool = False) -> dict:
    payload = json.loads(job["payload"] or "{}")
    pid = job["project_id"]
    wanted = payload.get("shots") or []
    draft = bool(payload.get("draft"))
    max_shots = int(payload.get("max_shots") or 0)
    if max_shots > 0:
        wanted = wanted[:max_shots]
    width, height = (840, 480) if draft else (1344, 768)
    steps = 12 if draft else 20

    if not already_started:
        _mark(job["id"], "running")
    script = render_adapter.write_script(pid, width, height)
    # recompute first: assets may have changed since the last read, and a take
    # recorded with a stale fingerprint would look broken the moment it lands
    shots = {s["shot_id"]: s for s in service.recompute_fingerprints(pid)}
    ordered = service.list_shots(pid)
    index_of = {s["shot_id"]: i for i, s in enumerate(ordered, start=1)}
    failures: list[str] = []
    done: list[str] = []

    for sid in wanted:
        sh = shots.get(sid)
        if not sh:
            failures.append(f"{sid}: 镜头已删除")
            continue
        out_dir = db.DATA_DIR / "tmp" / job["id"] / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        # chain needs the previous shot's clip to sit in this temp dir under the
        # name render_drama expects (NNNN.mp4); without it the renderer falls back
        # to the scene plate and every shot starts from the same frame.
        i = index_of.get(sid, 1)
        pinned = str(sh.get("first_frame") or "")
        if (str(sh.get("transition")) == "chain" and i > 1
                and not (pinned and Path(pinned).is_file())):
            prev_id = ordered[i - 2]["shot_id"]
            prev_take = service.latest_take(pid, prev_id)
            if prev_take and Path(prev_take["path"]).is_file():
                shutil.copy2(prev_take["path"], out_dir / f"{i - 1:04d}.mp4")
            else:
                failures.append(f"{sid}: chain 的上游 {prev_id} 还没有片段")
                continue
        cmd = render_cmd() + [str(script), "--out", str(out_dir), "--only", sid,
                              "--width", str(width), "--height", str(height),
                              "--steps", str(steps), "--no-upscale"]
        if pinned and Path(pinned).is_file():
            cmd += ["--first-frame", pinned]
        t0 = time.time()
        ensure_engine()
        # 提示词格式：仅影响本任务的子进程环境（执行器单线程，串行执行）
        # 扩散模型可按任务覆盖：fl2va（默认）/ ref2va（参考图专用）
        _unet = (payload.get("unet") or "").strip()
        # 若与上一个任务用的是同一个模型，就不要清显存 —— 让引擎复用已加载的权重，
        # 下一镜可以直接开始采样（省掉 1–6 分钟的重新加载）。
        _unet_key = _unet or "fl2va"
        _prev_unet = ""
        try:
            with db.txn() as conn:
                row = conn.execute(
                    "SELECT payload FROM jobs WHERE kind='render' AND state='done' "
                    "AND id<>? ORDER BY finished_at DESC LIMIT 1", (job["id"],)).fetchone()
            if row:
                _prev_unet = (json.loads(row["payload"] or "{}").get("unet") or "fl2va")
        except Exception:  # noqa: BLE001
            _prev_unet = ""
        # 实测结论：省掉 /free 会让显存逐镜累积碎片，
        # 夜批后段从 20 秒/步退化到 45 秒/步（s05 花了 23 分钟）。
        # 每镜清一次，代价只有几秒的重新加载，换来稳定 20 秒/步。
        print(f"[executor] {sid} unet={_unet_key} 每镜清显存（稳定 20 秒/步）", flush=True)
        if _unet:
            os.environ["H3_UNET"] = (_unet if _unet.endswith(".safetensors")
                                     else f"minimax_h3_{_unet}_pruned_int8_convrot.safetensors")
        _pm = (payload.get("prompt_mode") or "").strip().lower()
        _prev_pm = os.environ.get("H3_PROMPT_MODE")
        if _pm in ("compile", "concat"):
            os.environ["H3_PROMPT_MODE"] = _pm
        cp = _run_streaming(cmd, job["id"], sid)
        if _pm in ("compile", "concat"):
            if _prev_pm is None:
                os.environ.pop("H3_PROMPT_MODE", None)
            else:
                os.environ["H3_PROMPT_MODE"] = _prev_pm
        if _unet:
            os.environ.pop("H3_UNET", None)
        os.environ.pop("H3_NO_FREE", None)
        if cp["returncode"] != 0 and _is_oom(cp["output"]):
            # ComfyUI unloads its models on OOM but keeps stale allocations;
            # one clean retry after /free usually succeeds
            log_tail = (cp["output"] or "").strip().splitlines()[-1:] or [""]
            print(f"[executor] {sid} OOM ({log_tail[0][:80]}) — freeing VRAM and retrying")
            free_vram()
            time.sleep(10)
            cp = _run_streaming(cmd, job["id"], sid)
        elapsed = _shot_seconds(cp["output"]) or round(time.time() - t0, 1)
        if cp["returncode"] != 0:
            failures.append(f"{sid}: rc={cp['returncode']} {(cp['output'] or '')[-160:]}")
            continue
        expected = out_dir / f"{i:04d}.mp4"
        if expected.is_file() and expected.stat().st_size > 1024:
            produced = [expected]
        else:
            # never hand back the clip we copied in for chaining
            copy_name = f"{i - 1:04d}.mp4"
            produced = sorted(p for p in out_dir.glob("*.mp4")
                              if p.stat().st_size > 1024 and p.name != copy_name)
        if not produced:
            failures.append(f"{sid}: 没有产出片段")
            continue
        take = service.reserve_take(pid, sid)
        shutil.move(str(produced[0]), str(take["path"]))
        # 取证包：帧数/运镜幅度/响度/静音占比（失败不影响落库）
        try:
            from studio.core import metrics as metrics_mod  # noqa: PLC0415
            mt = metrics_mod.compute(Path(take["path"]))
        except Exception as e:  # noqa: BLE001
            mt = {"error": str(e)[:120]}
        service.record_take(pid, sid, take["path"], sh["fingerprint"], elapsed,
                            _peak(cp["output"]), note="draft" if draft else "executor",
                            metrics=mt, dialogue=sh.get("dialogue") or "")
        done.append(sid)
        PROGRESS[job["id"]] = {"shot_id": sid, "pct": 100, "detail": "done",
                               "started": PROGRESS.get(job["id"], {}).get("started", t0)}
    shutil.rmtree(db.DATA_DIR / "tmp" / job["id"], ignore_errors=True)
    state = "done" if not failures else "failed"
    remaining = [s for s in (payload.get("shots") or []) if s not in done]
    if state == "done" and max_shots > 0 and remaining:
        # pause-after-one: put the rest back as a fresh queued job
        service.enqueue_job(pid, "render", {**payload, "shots": remaining})
        state = "paused"
    # 夜间无人值守：失败的镜头自动重排队，避免早上发现一堆空洞
    if state == "failed" and not done:
        left = int(payload.get("retries_left", 0) or 0)
        failed_ids = [s for s in (payload.get("shots") or []) if s not in done]
        if left > 0 and failed_ids:
            print(f"[executor] {failed_ids} 失败，自动重试（剩余 {left} 次）", flush=True)
            service.enqueue_job(pid, "render",
                                {**payload, "shots": failed_ids,
                                 "retries_left": left - 1, "auto_retry": True})
    _finish(job["id"], state, "; ".join(failures))
    return {"job_id": job["id"], "state": state, "done": done, "failures": failures}


# ------------------------------------------------------------------ preview

def _run_preview(job: dict, already_started: bool = False) -> dict:
    payload = json.loads(job["payload"] or "{}")
    pid = job["project_id"]
    sid = payload.get("shot_id", "")
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    sh = shots.get(sid)
    if not sh:
        _finish(job["id"], "failed", f"{sid}: 镜头不存在")
        return {"job_id": job["id"], "state": "failed"}
    if not already_started:
        _mark(job["id"], "running")
    dest = db.DATA_DIR / "projects" / pid / "previews" / f"{sid}.jpg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    first = None
    # a chain shot's preview should start from the previous take's last frame,
    # otherwise the still is a completely different image and misleads the eye
    ordered = service.list_shots(pid)
    idx = next((i for i, s in enumerate(ordered) if s["shot_id"] == sid), 0)
    if str(sh.get("transition")) == "chain" and idx > 0:
        prev = service.latest_take(pid, ordered[idx - 1]["shot_id"])
        if prev and Path(prev["path"]).is_file():
            frame = db.DATA_DIR / "tmp" / f"{job['id']}_first.jpg"
            frame.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-sseof", "-0.15",
                            "-i", prev["path"], "-frames:v", "1", "-update", "1",
                            "-q:v", "2", str(frame)], check=False)
            if frame.is_file():
                first = frame
    cmd = still_cmd() + ["--prompt", sh["prompt"] or sh["camera"] or sh["shot_id"],
                         "--out", str(dest), "--duration", "3", "--seed", "42",
                         "--width", "768", "--height", "448"]
    if first:
        cmd += ["--first-frame", str(first)]
    cp = subprocess.run(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True)
    if cp.returncode != 0 or not dest.is_file():
        _finish(job["id"], "failed", (cp.stdout or "")[-200:])
        return {"job_id": job["id"], "state": "failed"}
    service.record_preview(pid, sid, str(dest), sh["fingerprint"])
    _finish(job["id"], "done", "")
    return {"job_id": job["id"], "state": "done", "preview": str(dest)}


# ------------------------------------------------------------------ asset

def _run_asset(job: dict, already_started: bool = False) -> dict:
    """文生图：一次任务出 1–4 张，产物落到 studio-data/generated/<日期>/。"""
    payload = json.loads(job["payload"] or "{}")
    prompt = str(payload.get("prompt") or "").strip()
    kind = str(payload.get("kind") or "character")
    width = int(payload.get("width") or 2048)
    height = int(payload.get("height") or 2048)
    steps = int(payload.get("steps") or 6)
    count = max(1, min(4, int(payload.get("count") or 1)))
    base_seed = int(payload.get("seed", -1))
    project_id = str(payload.get("project_id") or "")
    group_id = str(payload.get("group_id") or "")
    refs = [str(x) for x in (payload.get("refs") or []) if str(x)]

    if not already_started:
        _mark(job["id"], "running")
    if not prompt:
        _finish(job["id"], "failed", "提示词为空")
        return {"job_id": job["id"], "state": "failed"}

    # 上一个任务也是文生图时不用清显存：模型已在显存里，省掉 1–2 分钟重载
    prev_kind = ""
    try:
        with db.txn() as conn:
            row = conn.execute(
                "SELECT kind FROM jobs WHERE state='done' AND id<>? "
                "ORDER BY finished_at DESC LIMIT 1", (job["id"],)).fetchone()
        if row:
            prev_kind = row["kind"]
    except Exception:  # noqa: BLE001
        prev_kind = ""

    ensure_engine()
    day = time.strftime("%Y%m%d")
    out_dir = db.DATA_DIR / "generated" / day
    out_dir.mkdir(parents=True, exist_ok=True)

    ids: list[str] = []
    failures: list[str] = []
    for i in range(count):
        cand_id = uuid.uuid4().hex[:12]
        out = out_dir / f"{cand_id}.png"
        seed = (base_seed + i) if base_seed >= 0 else -1
        cmd = asset_cmd() + [
            "--prompt", prompt, "--out", str(out),
            "--width", str(width), "--height", str(height),
            "--steps", str(steps), "--seed", str(seed),
        ]
        if i > 0 or prev_kind == "asset":
            cmd.append("--no-free")
        for ref in refs:
            cmd += ["--ref", ref]
        # 批次进度：第几张 + 段内百分比由 _run_streaming 覆盖
        PROGRESS[job["id"]] = {"shot_id": f"第 {i + 1}/{count} 张", "pct": None,
                               "stage": "starting", "detail": "已提交，等待引擎接手",
                               "started": PROGRESS.get(job["id"], {}).get("started") or time.time()}
        cp = _run_streaming(cmd, job["id"], f"第 {i + 1}/{count} 张")
        info = _last_json(cp["output"])
        if cp["returncode"] != 0 or not out.is_file() or out.stat().st_size < 1024:
            tail = (cp["output"] or "").strip().splitlines()[-1:] or [""]
            failures.append(f"第 {i + 1} 张失败: {tail[0][:160]}")
            continue
        actual_seed = int(info.get("seed", seed))
        elapsed = float(info.get("elapsed_s") or 0.0)
        c = assets.create_candidate(
            kind=kind, prompt=prompt, width=width, height=height,
            seed=actual_seed, steps=steps, path=str(out),
            elapsed_s=elapsed, cand_id=cand_id,
            project_id=project_id, group_id=group_id,
        )
        ids.append(c["id"])
        PROGRESS[job["id"]] = {"shot_id": f"第 {i + 1}/{count} 张", "pct": 100,
                               "stage": "done", "detail": "已生成",
                               "started": PROGRESS.get(job["id"], {}).get("started") or time.time()}

    state = "done" if not failures else "failed"
    _finish(job["id"], state, "; ".join(failures))
    return {"job_id": job["id"], "state": state, "candidates": ids,
            "failures": failures}


def _last_json(text: str) -> dict:
    """取子进程输出里最后一个以 { 开头的行（gen_image.py 的终稿 JSON）。"""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return {}


# ------------------------------------------------------------------ helpers

def _comfy_log() -> Path:
    env = os.environ.get("STUDIO_COMFY_LOG")
    if env:
        return Path(env).expanduser()
    # 引擎 systemd 单元把 stdout 追加到这个文件；找不到就回落到同一路径，
    # 这样即使文件还没被创建，后续也会在正确位置出现
    for cand in (Path.home() / "openvideo" / "ComfyUI-up" / ".cache" / "comfyui-045.log",
                 Path.home() / "openvideo" / "video-agent" / ".cache" / "comfyui-8188.log",
                 Path.home() / ".openvideo" / "logs" / "comfyui-8188.log",
                 Path.home() / "openvideo" / "ComfyUI" / "comfyui.log"):
        if cand.is_file():
            return cand
    return Path.home() / "openvideo" / "video-agent" / ".cache" / "comfyui-8188.log"


LOAD_HINTS = ("Requested to load", "loaded completely", "loaded partially",
              "model weight dtype", "Using MixedPrecisionOps", "VAE load device",
              "CLIP/text encoder model load", "got prompt")


def _tail_progress(log: Path, job_id: str, shot_id: str, started: float,
                   stop: threading.Event, offset: int = 0) -> None:
    """Watch the ComfyUI log from `offset` on, so an old job's bar is not reused.

    三个阶段分别报告，避免"加载模型那两分钟"看起来像卡死：
      - loading: 刚提交，正在往显存搬模型（没有百分比，但有 stage）
      - sampling: 有 n/total 进度条
      - idle: 超过 90 秒没有任何新日志 → 标记可能卡住
    """
    pos = offset
    last_line = ""
    last_change = time.time()
    while not stop.is_set():
        try:
            if log.is_file():
                size = log.stat().st_size
                if size > pos:
                    with log.open("rb") as fh:
                        fh.seek(pos)
                        chunk = fh.read().decode("utf-8", "ignore")
                    pos = size
                    last_change = time.time()
                    cur = PROGRESS.get(job_id) or {}
                    pct = cur.get("pct")
                    stage = cur.get("stage") or "loading"
                    for line in reversed(chunk.splitlines()):
                        # 新的 prompt = 重试或下一镜：进度必须重置，否则会读到上一轮的 100%
                        if "got prompt" in line:
                            pct = None
                            stage = "loading"
                            last_line = "新任务已提交，正在加载模型"
                            break
                        got = parse_progress(line)
                        if got is not None:
                            # 进度只增不减：引擎会重复打印同一批 bar
                            pct = max(got, pct or 0)
                            stage = "sampling" if pct < 100 else "decoding"
                            last_line = line.strip()[-120:]
                            break
                        if any(h in line for h in LOAD_HINTS):
                            # 只有"采样已经跑完（100%）"之后的加载才是解码；
                            # 5% 时的加载 = 重试/换模型，绝不能显示成"解码中"
                            if (pct or 0) >= 100:
                                stage = "decoding"
                                last_line = "正在解码成片（写视频文件）"
                            else:
                                stage = "loading"
                                last_line = line.strip()[-120:]
                            break
                        # 更细的阶段，避免"两分钟没动静"看起来像卡死
                        if "MiniMaxH3TEModel_" in line:
                            if (pct or 0) < 100:
                                stage = "encoding"
                                last_line = "正在编码提示词（文本→向量，约 1–2 分钟）"
                            break
                        if "Requested to load MiniMaxH3" in line and "TE" not in line:
                            if (pct or 0) < 100:
                                stage, last_line = "loading", "正在加载扩散模型到显存"
                            break
                    # 已经开始采样后，pct 不再清空（否则进度条会从 95% 掉回 0）
                    if stage in ("loading", "encoding") and (pct or 0) < 100:
                        pct = None
                    # 非 starting 阶段出现 0%，一律视为"还没真正开始"，交给阶段文案描述
                    if pct == 0 and stage != "starting":
                        pct = None
                    PROGRESS[job_id] = {"shot_id": shot_id, "pct": pct,
                                        "stage": stage, "detail": last_line,
                                        "started": started}
        except OSError:
            pass
        # 兜底：tqdm 用 \r 覆写，增量块偶尔抓不到；直接看文件末尾。
        # 只有本任务已经开始采样后才用，否则会把上一镜残留的 100% 当成这一镜的进度。
        try:
            cur_stage = (PROGRESS.get(job_id) or {}).get("stage")
            if log.is_file() and cur_stage == "sampling":
                with log.open("rb") as fh:
                    fh.seek(max(0, log.stat().st_size - 4096))
                    tail = fh.read().decode("utf-8", "ignore").replace("\r", "\n")
                for line in reversed(tail.splitlines()):
                    got = parse_progress(line)
                    if got is not None:
                        cur = PROGRESS.get(job_id) or {}
                        if cur.get("pct") != got:
                            cur.update({"pct": got, "stage": "sampling",
                                        "detail": line.strip()[-120:]})
                            PROGRESS[job_id] = cur
                            last_change = time.time()
                        break
        except OSError:
            pass

        beat = (PROGRESS.get(job_id) or {}).get("beat") or 0.0
        silent = time.time() - max(last_change, beat)
        # 加载模型时本来就没有日志（实测可持续 4–6 分钟），别误报卡住
        cur_stage = (PROGRESS.get(job_id) or {}).get("stage")
        # 15 秒镜一步要 60–120 秒，90 秒阈值必然误报；按时长放大
        limit = 600 if cur_stage in ("loading", "encoding", "decoding") else 90
        dur = float((PROGRESS.get(job_id) or {}).get("duration") or 0) or 0.0
        if dur > 0:
            limit = int(limit * max(1.0, dur / 5.0))
        if silent > limit:
            cur = PROGRESS.get(job_id) or {}
            cur.update({"stage": "stalled", "silent_s": round(silent),
                        "detail": f"已经 {round(silent)} 秒没有任何引擎输出"})
            PROGRESS[job_id] = cur
        stop.wait(2.0)


def _run_streaming(cmd: list[str], job_id: str, shot_id: str) -> dict:
    """Run a shot, streaming stdout so the UI can show a live percentage."""
    started = PROGRESS.get(job_id, {}).get("started") or time.time()
    # 一开始不能给 pct=0：前端会先显示 "0%"，几十秒后才变"加载模型…"，看着像卡在 0
    PROGRESS[job_id] = {"shot_id": shot_id, "pct": None, "stage": "starting",
                        "detail": "已提交，等待引擎接手", "started": started}
    stop = threading.Event()
    log = _comfy_log()
    offset = log.stat().st_size if log.is_file() else 0
    watcher = threading.Thread(target=_tail_progress,
                               args=(log, job_id, shot_id, started, stop, offset),
                               daemon=True, name="comfy-progress")
    watcher.start()
    proc = subprocess.Popen(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1)
    RUNNING[job_id] = proc
    chunks: list[str] = []
    assert proc.stdout is not None
    try:
        log_dir = db.DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{job_id}.log"
        with log_path.open("w", encoding="utf-8") as logfh:
            logfh.write(f"# job {job_id} shot {shot_id} cmd {' '.join(cmd)}\n")
            for line in proc.stdout:
                chunks.append(line)
                # 子进程只要有输出就算"活着"（长镜单步慢，不能只靠引擎日志判断）
                cur = PROGRESS.get(job_id) or {}
                cur["beat"] = time.time()
                if not cur.get("stage") or cur.get("stage") == "stalled":
                    cur["stage"] = "working"
                    cur["detail"] = line.strip()[-120:] or "引擎在跑"
                PROGRESS[job_id] = cur
                logfh.write(line)
                logfh.flush()
                pct = parse_progress(line)
                if pct is not None:
                    PROGRESS[job_id] = {"shot_id": shot_id, "pct": pct,
                                        "detail": line.strip()[:120], "started": started}
    finally:
        proc.wait()
        stop.set()
        RUNNING.pop(job_id, None)
    tail = "".join(chunks[-40:])
    if tail.strip():
        try:
            with db.txn() as conn:
                db.emit(conn, "", shot_id, "job.log_tail", {"job_id": job_id, "tail": tail[-4000:]})
        except Exception:  # noqa: BLE001
            pass
    return {"returncode": proc.returncode, "output": "".join(chunks), "log": str(log_path)}


def _peak(text: str) -> int:
    vals = [int(m) for m in PEAK_RE.findall(text or "")]
    return max(vals) if vals else 0


def _shot_seconds(text: str) -> float:
    vals = [float(m) for _, m in SHOT_OK_RE.findall(text or "")]
    return max(vals) if vals else 0.0


def _mark(jid: str, state: str) -> None:
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state=?, started_at=? WHERE id=?",
                     (state, db.now(), jid))
        db.emit(conn, "", "", f"job.{state}", {"job_id": jid})


def _finish(jid: str, state: str, error: str) -> None:
    with db.txn() as conn:
        conn.execute("UPDATE jobs SET state=?, error=?, finished_at=? WHERE id=?",
                     (state, error, db.now(), jid))
        db.emit(conn, "", "", f"job.{state}", {"job_id": jid, "error": error})
