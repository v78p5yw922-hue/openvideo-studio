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
import sys
import threading
import time
from pathlib import Path

from ..core import db, service
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


def kill(job_id: str) -> bool:
    """Terminate the renderer for a job. Returns True if something was killed."""
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
    return True


OOM_RE = re.compile(r"out of memory|OutOfMemory|exceed allowed memory", re.IGNORECASE)


def free_vram(port: int = 8188) -> bool:
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
        cp = _run_streaming(cmd, job["id"], sid)
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
        service.record_take(pid, sid, take["path"], sh["fingerprint"], elapsed,
                            _peak(cp["output"]), note="draft" if draft else "executor")
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
                         "--width", "768", "--height", "432"]
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


# ------------------------------------------------------------------ helpers

def _comfy_log() -> Path:
    env = os.environ.get("STUDIO_COMFY_LOG")
    if env:
        return Path(env).expanduser()
    for cand in (Path.home() / ".openvideo" / "logs" / "comfyui-8188.log",
                 Path.home() / "openvideo/video-agent/.cache/comfyui-8188.log"):
        if cand.is_file():
            return cand
    return Path.home() / ".openvideo" / "logs" / "comfyui-8188.log"


def _tail_progress(log: Path, job_id: str, shot_id: str, started: float,
                   stop: threading.Event, offset: int = 0) -> None:
    """Watch the ComfyUI log from `offset` on, so an old job's bar is not reused."""
    pos = offset
    while not stop.is_set():
        try:
            if log.is_file():
                size = log.stat().st_size
                if size > pos:
                    with log.open("rb") as fh:
                        fh.seek(pos)
                        chunk = fh.read().decode("utf-8", "ignore")
                    pos = size
                    for line in reversed(chunk.splitlines()):
                        pct = parse_progress(line)
                        if pct is not None:
                            PROGRESS[job_id] = {"shot_id": shot_id, "pct": pct,
                                                "detail": line.strip()[-120:],
                                                "started": started}
                            break
        except OSError:
            pass
        stop.wait(2.0)


def _run_streaming(cmd: list[str], job_id: str, shot_id: str) -> dict:
    """Run a shot, streaming stdout so the UI can show a live percentage."""
    started = PROGRESS.get(job_id, {}).get("started") or time.time()
    PROGRESS[job_id] = {"shot_id": shot_id, "pct": 0, "detail": "starting",
                        "started": started}
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
        for line in proc.stdout:
            chunks.append(line)
            pct = parse_progress(line)
            if pct is not None:
                PROGRESS[job_id] = {"shot_id": shot_id, "pct": pct,
                                    "detail": line.strip()[:120], "started": started}
    finally:
        proc.wait()
        stop.set()
        RUNNING.pop(job_id, None)
    return {"returncode": proc.returncode, "output": "".join(chunks)}


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
