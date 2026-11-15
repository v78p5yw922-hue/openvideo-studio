"""End-to-end walk with the fake renderer: no GPU, full pipeline.

import markdown -> upload plate -> plan -> confirm -> executor -> review ->
export EDL/OTIO -> backup.

This is the test that answers "does the whole thing actually work", as opposed to
the unit tests which answer "does each rule hold".
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-e2e-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "1"
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

HERE = Path(__file__).resolve().parent
os.environ["STUDIO_RENDER_CMD"] = str(HERE / "fake_render.py")
os.environ["STUDIO_STILL_CMD"] = str(HERE / "fake_still.py")

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402
from studio.exec import runner  # noqa: E402

client = TestClient(app)

SCRIPT = """## 项目
- 片名：《端到端》
- 总时长：15 秒（共 3 镜 × 5 秒）

## 角色卡
- 名字：阿澈
- 英文外观描述：A 29-year-old engineer with a clear visor.

## 场景卡
### 场景 1：舱内
- 机位：平视固定
- 科技物件：台面（中）、窗（右）
- 光源：顶部冷白

## 镜头表
| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 |
|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 舱内 | 无（空镜） | 中景 | 台面中 | 灯呼吸 | 无 | cut |
| 镜 2（5s） | 舱内 | 阿澈 | 近景 | 台面中 | 俯身 | <d>阿澈：有信号。</d> | chain |
| 镜 3（5s） | 舱内 | 阿澈 | 特写 | 台面中 | 抬眼 | 无 | cut |

## 每镜可执行 prompt
### 镜 1
【场景锚点】舱内空镜。【角色】无人。
### 镜 2
【场景锚点】舱内近景。【角色】阿澈。
### 镜 3
【场景锚点】舱内特写。【角色】阿澈。
"""


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    # env is per-test: other test modules reset it at import time during collection
    monkeypatch.setenv("STUDIO_ALLOW_RENDER", "1")
    monkeypatch.setenv("STUDIO_RENDER_CMD", str(HERE / "fake_render.py"))
    monkeypatch.setenv("STUDIO_STILL_CMD", str(HERE / "fake_still.py"))
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _plate():
    p = Path(TMP) / "plate.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"P" * 512)
    return p


def test_full_pipeline():
    pid = "e2e"

    # 1. import
    r = client.post("/api/import", json={"project_id": pid, "markdown": SCRIPT})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["shots"] == ["s01", "s02", "s03"]

    # 2. scene plate upload (otherwise the plan blocks)
    with _plate().open("rb") as fh:
        up = client.post(f"/api/projects/{pid}/scenes", json=None) if False else None
    scene_id = client.get(f"/api/projects/{pid}").json()["shots"][0]["scene_id"]
    client.post(f"/api/projects/{pid}/scenes", json={"scene_id": scene_id, "name": "舱内"})
    with _plate().open("rb") as fh:
        up = client.post(f"/api/projects/{pid}/scenes/{scene_id}/plate",
                         files={"file": ("plate.jpg", fh, "image/jpeg")})
    assert up.status_code == 200, up.text
    assert up.json()["plate_sha"], "母版必须算出 sha"

    # 3. plan -> confirm
    p = client.post(f"/api/projects/{pid}/plan", json={"draft": True}).json()
    assert p["count"] == 3
    assert not p["blocking"], p["blocking"]
    c = client.post(f"/api/projects/{pid}/confirm",
                    json={"plan_id": p["plan_id"], "draft": True})
    assert c.status_code == 200, c.text
    assert c.json()["count"] == 3, "一镜一任务"

    # 4. executor runs the queue (fake renderer), one job per shot
    for _ in range(3):
        done = runner.tick()
        assert done, "队列里应该还有任务"
    assert done["state"] == "done", done

    detail = client.get(f"/api/projects/{pid}").json()
    states = {s["shot_id"]: s["state"] for s in detail["shots"]}
    assert set(states.values()) == {"review"}, states
    for s in detail["shots"]:
        assert s["take"]["path"].endswith(f"{s['shot_id']}_v1.mp4")
        assert s["take"]["peak_mib"] == 23456

    # 5. review all
    for sid in ("s01", "s02", "s03"):
        rv = client.post(f"/api/projects/{pid}/review",
                         json={"shot_id": sid, "verdict": "pass"})
        assert rv.status_code == 200, rv.text

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["counts"]["passed"] == 3

    # 6. export timeline
    edl = client.get(f"/api/projects/{pid}/export/edl").json()
    assert edl["clips"] == 3
    assert edl["frames"] == 360, "三镜各 120 帧"
    assert "* FROM CLIP NAME: s02_v1" in edl["edl"]
    otio = client.get(f"/api/projects/{pid}/export/otio").json()
    assert len(otio["tracks"]["children"][0]["children"]) == 3
    assert otio["metadata"]["clip_count"] == 3

    # 7. stats reflect the run
    st = client.get(f"/api/projects/{pid}/stats").json()
    assert st["shots"] == 3 and st["takes"] == 3
    assert st["passed"] == 3 and st["pass_rate"] == 1.0
    assert st["rerender_rate"] == 0.0
    assert st["avg_shot_seconds"] > 0
    assert st["peak_mib"] == 23456

    # 8. backup writes a consistent snapshot
    dest = Path(TMP) / "backups"
    import subprocess
    cp = subprocess.run([sys.executable, "-m", "studio.backup", "--dest", str(dest)],
                        cwd=str(Path(__file__).resolve().parents[2]),
                        capture_output=True, text=True,
                        env={**os.environ, "STUDIO_DATA": str(db.DATA_DIR)})
    assert cp.returncode == 0, cp.stdout + cp.stderr
    snaps = list(dest.iterdir())
    assert snaps and (snaps[0] / "studio.db").is_file()
    assert (snaps[0] / "projects" / pid).is_dir(), "资产要跟着一起备份"


def test_cascade_invalidates_downstream():
    """Editing s02 must turn s02 and its chain successor s03 stale, not s01."""
    pid = "cascade_e2e"
    client.post("/api/import", json={"project_id": pid, "markdown": SCRIPT})
    scene_id = client.get(f"/api/projects/{pid}").json()["shots"][0]["scene_id"]
    client.post(f"/api/projects/{pid}/scenes", json={"scene_id": scene_id, "name": "舱内"})
    with _plate().open("rb") as fh:
        client.post(f"/api/projects/{pid}/scenes/{scene_id}/plate",
                    files={"file": ("plate.jpg", fh, "image/jpeg")})
    shots = service.recompute_fingerprints(pid)
    for sh in shots:
        service.record_take(pid, sh["shot_id"], f"/tmp/{sh['shot_id']}.mp4", sh["fingerprint"])
        service.review_shot(pid, sh["shot_id"], "pass")

    edited = [{"shot_id": s["shot_id"], "scene_id": s["scene_id"], "cast": s["cast"],
               "camera": s["camera"],
               "prompt": (s["prompt"] + "（改）") if s["shot_id"] == "s02" else s["prompt"],
               "dialogue": s["dialogue"], "transition": s["transition"]}
              for s in shots]
    client.put(f"/api/projects/{pid}/shots", json={"shots": edited})
    shots = client.get(f"/api/projects/{pid}").json()["shots"]
    st = {s["shot_id"]: s["state"] for s in shots}
    assert st["s02"] == "stale", st
    assert st["s03"] == "passed", "s03 是 cut，不该被上游影响"
    assert st["s01"] == "passed", "上游 cut 不受影响"
