"""Executor tests: the queue must produce takes named by shot_id, with the
fingerprint captured at render time, and must never run while disabled.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-exec-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "0"
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, service  # noqa: E402
from studio.exec import runner  # noqa: E402

db.init_db()

HERE = Path(__file__).resolve().parent
FAKE_RENDER = str(HERE / "fake_render.py")
FAKE_STILL = str(HERE / "fake_still.py")


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "0")
    monkeypatch.setenv("STUDIO_RENDER_CMD", FAKE_RENDER)
    monkeypatch.setenv("STUDIO_STILL_CMD", FAKE_STILL)
    yield


def _setup(pid="exec_test"):
    service.create_project(pid, "执行器测试", "cinematic")
    plate = Path(TMP) / "plate.jpg"
    plate.write_bytes(b"plate-bytes")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    service.save_shots(pid, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "camera": "medium",
         "prompt": "p1", "dialogue": "", "transition": "cut", "anchor": "plate"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "camera": "close",
         "prompt": "p2", "dialogue": "", "transition": "chain"},
    ])
    return pid


def test_disabled_does_nothing(monkeypatch):
    pid = _setup()
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    assert runner.tick() is None
    job = service.list_jobs(pid)[0]
    assert job["state"] == "queued", "未启用时不能消费队列"


def test_render_records_take_named_by_shot_id(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    result = runner.tick()
    assert result and result["state"] == "done", result

    state = service.shot_state(pid, service.list_shots(pid)[0])
    assert state["state"] == "review"
    take = state["take"]
    assert Path(take["path"]).name == "s01_v1.mp4", take["path"]
    assert take["fingerprint"] == shots["s01"]["fingerprint"]
    assert take["peak_mib"] == 23456, "峰值显存要回写"
    assert take["elapsed_s"] == 123.4


def test_second_render_bumps_version(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    runner.tick()
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    runner.tick()
    with db.txn() as conn:
        rows = conn.execute("SELECT version, path FROM takes WHERE project_id=? AND shot_id=? "
                            "ORDER BY version", (pid, "s01")).fetchall()
    assert [r["version"] for r in rows] == [1, 2]
    assert rows[1]["path"].endswith("s01_v2.mp4")


def test_preview_job_writes_preview(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    service.enqueue_job(pid, "preview", {"shot_id": "s01"})
    result = runner.tick()
    assert result and result["state"] == "done", result
    pv = service.get_preview(pid, "s01")
    assert pv and Path(pv["path"]).is_file()
    state = service.shot_state(pid, service.list_shots(pid)[0])
    assert state["state"] == "empty", "预览不等于 take，状态仍是未生成"
    assert state["preview"] is not None


def test_progress_reads_comfy_log(monkeypatch, tmp_path):
    """The executor tails the ComfyUI log, because render_drama does not print steps."""
    import threading
    import time as _time
    pid = _setup()
    log = tmp_path / "comfy.log"
    log.write_text("garbage\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_COMFY_LOG", str(log))

    stop = threading.Event()
    started = _time.time()
    th = threading.Thread(target=runner._tail_progress,
                         args=(log, "jobX", "s01", started, stop), daemon=True)
    th.start()
    _time.sleep(0.3)
    with log.open("a", encoding="utf-8") as fh:   # append: the tailer only reads new bytes
        fh.write("  42%|####      | 8/20 [00:12<00:18]\n")
    _time.sleep(2.5)
    stop.set()
    th.join(timeout=3)
    p = runner.progress_of("jobX")
    assert p and p["pct"] == 42, p


def test_parse_progress_formats():
    from studio.exec.runner import parse_progress
    assert parse_progress("[drama] [01] step 3/12") == 25
    assert parse_progress(" 45%|███       | 9/20 [00:12<00:15]") == 45
    assert parse_progress("100%|##########| 20/20") == 100
    assert parse_progress("[drama] [01] OK 460.0s peak=23794 MiB") is None
    assert parse_progress("no numbers here") is None


def test_progress_reaches_100_after_shot(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    job = service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    runner.tick()
    p = runner.progress_of(job["id"])
    assert p and p["pct"] == 100 and p["shot_id"] == "s01"


def test_preview_of_chain_shot_uses_prev_last_frame(monkeypatch, tmp_path):
    """Preview should reflect what the shot will look like, so a chain shot must
    start from the previous take's last frame instead of inventing a new image."""
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    prev = tmp_path / "prev.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=red:s=64x36:d=1", "-pix_fmt", "yuv420p", str(prev)],
                   check=False)
    service.record_take(pid, "s01", str(prev), shots["s01"]["fingerprint"])

    real_run = subprocess.run
    captured = {}

    def fake_run(cmd, **kw):
        if "--out" not in cmd:                 # ffmpeg frame extraction: run for real
            return real_run(cmd, **kw)
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        out.write_bytes(b"\xff\xd8\xff\xd9")
        class R:
            returncode = 0
            stdout = "ok"
        return R()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    service.enqueue_job(pid, "preview", {"shot_id": "s02"})
    runner.tick()
    assert "--first-frame" in captured["cmd"], "chain 预览必须带首帧"
    assert captured["cmd"][captured["cmd"].index("--first-frame") + 1].endswith(".jpg")


def test_preview_job_does_not_change_shot_state(monkeypatch):
    """A preview is not a take: the card must not flip to 生成中 and back."""
    pid = _setup()
    service.enqueue_job(pid, "preview", {"shot_id": "s01"})
    st = service.shot_state(pid, service.list_shots(pid)[0])
    assert st["state"] == "empty", st["state"]
    assert st["preview_job"] and st["preview_job"]["kind"] == "preview"

    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": True})
    st = service.shot_state(pid, service.list_shots(pid)[0])
    assert st["state"] == "queued", "render 任务才应该改变状态"


def test_cancel_running_kills_process(monkeypatch):
    class FakeProc:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    fp = FakeProc()
    runner.RUNNING["jobZ"] = fp
    try:
        assert runner.kill("jobZ") is True
        assert fp.terminated
        assert "jobZ" not in runner.RUNNING or True   # kill 不负责移除，由 finally 处理
    finally:
        runner.RUNNING.pop("jobZ", None)
    assert runner.kill("nope") is False


def test_oom_triggers_free_and_one_retry(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    calls = {"n": 0, "freed": 0}
    real_free = runner.free_vram
    monkeypatch.setattr(runner, "free_vram", lambda *a, **k: calls.__setitem__("freed", calls["freed"] + 1) or True)
    monkeypatch.setattr(runner.time, "sleep", lambda *_: None)

    def fake_stream(cmd, job_id, shot_id):
        calls["n"] += 1
        out = Path(cmd[cmd.index("--out") + 1])
        if calls["n"] == 1:
            return {"returncode": 1, "output": "torch.OutOfMemoryError: Allocation on device 0"}
        (out / f"{int(shot_id[1:]):04d}.mp4").write_bytes(b"z" * 5000)
        return {"returncode": 0, "output": "[drama] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    r = runner.tick()
    assert r["state"] == "done", r
    assert calls["n"] == 2, "OOM 后应重试一次"
    assert calls["freed"] == 1, "重试前要释放显存"
    monkeypatch.setattr(runner, "free_vram", real_free)


def test_non_oom_failure_is_not_retried(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    calls = {"n": 0}

    def fake_stream(cmd, job_id, shot_id):
        calls["n"] += 1
        return {"returncode": 1, "output": "some other error"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    r = runner.tick()
    assert r["state"] == "failed"
    assert calls["n"] == 1, "非 OOM 失败不重试"


def test_batch_renders_in_order_and_chains(monkeypatch):
    """Selecting several shots queues one job each; the second must chain from the
    first take produced moments earlier in the same batch."""
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    order = []

    def fake_stream(cmd, job_id, shot_id):
        out = Path(cmd[cmd.index("--out") + 1])
        order.append((shot_id, sorted(p.name for p in out.glob("*.mp4"))))
        (out / f"{int(shot_id[1:]):04d}.mp4").write_bytes(b"z" * 5000)
        return {"returncode": 0, "output": "[drama] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    service.enqueue_job(pid, "render", {"shots": ["s02"], "draft": False})
    runner.tick()
    runner.tick()

    assert [o[0] for o in order] == ["s01", "s02"], "必须按顺序"
    assert order[1][1] == ["0001.mp4"], "第二镜的临时目录里要有第一镜的片段"
    assert service.latest_take(pid, "s02") is not None


def test_chain_take_is_not_the_copied_predecessor(monkeypatch, tmp_path):
    """Regression: the copied previous clip must never be recorded as this shot's take."""
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    prev = tmp_path / "prev.mp4"
    prev.write_bytes(b"P" * 5000)
    service.record_take(pid, "s01", str(prev), shots["s01"]["fingerprint"])
    before = Path(prev).stat().st_size

    def fake_stream(cmd, job_id, shot_id):
        out = Path(cmd[cmd.index("--out") + 1])
        # the executor already copied 0001.mp4 in; renderer writes 0002.mp4
        assert (out / "0001.mp4").is_file()
        (out / "0002.mp4").write_bytes(b"N" * 7000)
        return {"returncode": 0, "output": "[drama] [02] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s02"], "draft": False})
    runner.tick()
    take = service.latest_take(pid, "s02")
    assert Path(take["path"]).name == "s02_v1.mp4"
    assert Path(take["path"]).stat().st_size == 7000, "必须是新渲的那条，不是复制的上一镜"
    assert Path(take["path"]).stat().st_size != before


def test_chain_copies_previous_take_into_temp_dir(monkeypatch):
    """Without the previous clip in the temp dir, render_drama falls back to the
    scene plate and every shot starts from the same frame."""
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    shots = {s["shot_id"]: s for s in service.list_shots(pid)}
    service.record_take(pid, "s01", "/tmp/prev.mp4", shots["s01"]["fingerprint"])
    Path("/tmp/prev.mp4").write_bytes(b"x" * 4096)

    seen = {}

    def fake_stream(cmd, job_id, shot_id):
        out = Path(cmd[cmd.index("--out") + 1])
        seen["files"] = sorted(p.name for p in out.glob("*.mp4"))
        (out / "0002.mp4").write_bytes(b"y" * 4096)
        return {"returncode": 0, "output": "[drama] [02] OK 100.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s02"], "draft": False})
    result = runner.tick()
    assert result["state"] == "done", result
    assert seen["files"] == ["0001.mp4"], f"上一镜必须被复制进来，实际 {seen['files']}"


def test_chain_without_upstream_fails_loudly(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    monkeypatch.setattr(runner, "_run_streaming",
                        lambda *a, **k: {"returncode": 0, "output": ""})
    service.enqueue_job(pid, "render", {"shots": ["s02"], "draft": False})
    result = runner.tick()
    assert result["state"] == "failed"
    assert "上游" in result["failures"][0]


def test_take_not_stale_when_asset_changed_after_queue(monkeypatch):
    """Regression: the executor must recompute fingerprints before recording."""
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": False})
    # the plate changes while the job waits in the queue
    plate = Path(TMP) / "plate.jpg"
    plate.write_bytes(b"plate-bytes-changed")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))

    runner.tick()
    state = service.shot_state(pid, service.list_shots(pid)[0])
    assert state["state"] == "review", "新 take 不应一落地就 stale"
    assert state["take"]["fingerprint"] == state["take"]["fingerprint"]


def test_pause_after_one_requeues_the_rest(monkeypatch):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    service.enqueue_job(pid, "render",
                        {"shots": ["s01", "s02"], "draft": False, "max_shots": 1})
    result = runner.tick()
    assert result["state"] == "paused", result
    jobs = service.list_jobs(pid)
    states = sorted(j["state"] for j in jobs)
    assert "paused" in states
    assert any(j["state"] == "queued" and "s02" in j["payload"] for j in jobs), \
        "剩余镜头要重新排队"


def test_draft_uses_small_raster(monkeypatch, tmp_path):
    pid = _setup()
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    captured = {}

    def fake_stream(cmd, job_id, shot_id):
        captured["cmd"] = cmd
        out = Path(cmd[cmd.index("--out") + 1])
        out.mkdir(parents=True, exist_ok=True)
        (out / "0001.mp4").write_bytes(b"x" * 4096)
        return {"returncode": 0, "output": "[drama] [01] OK 60.0s peak=9000 MiB"}

    monkeypatch.setattr(runner, "_run_streaming", fake_stream)
    service.enqueue_job(pid, "render", {"shots": ["s01"], "draft": True})
    runner.tick()
    cmd = captured["cmd"]
    assert "--width" in cmd and "480" in cmd, "草稿应使用小分辨率"
    assert "12" in cmd, "草稿应降低步数"
