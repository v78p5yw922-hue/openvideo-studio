"""Adopt a take + assemble the film."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-asm-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "asm_test"


@pytest.fixture(autouse=True)
def clean(tmp_path):
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "拼接测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
    ])
    yield


def _clip(path: Path, color="0x224488"):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c={color}:s=256x144:d=1:r=24", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", str(path)], check=False)
    return path


def test_adopt_marks_take_valid():
    clip = _clip(Path(TMP) / "a.mp4")
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, "s01", str(clip), "deadbeef")
    assert service.shot_state(PID, service.list_shots(PID)[0])["state"] == "stale"
    r = service.adopt_take(PID, "s01")
    assert r["state"] == "review"


def test_assemble_produces_file():
    for sid in ("s01", "s02"):
        _clip(Path(TMP) / f"{sid}.mp4")
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    for sid in ("s01", "s02"):
        service.record_take(PID, sid, str(Path(TMP) / f"{sid}.mp4"),
                            shots[sid]["fingerprint"])
    r = service.assemble(PID, width=256, height=144, with_subs=False)
    out = Path(r["out"])
    assert out.is_file() and out.stat().st_size > 1000
    assert r["shots"] == 2
    r2 = client.get(r["url"])
    assert r2.status_code == 200


def test_assemble_blocks_when_a_shot_is_missing():
    clip = _clip(Path(TMP) / "only.mp4")
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, "s01", str(clip), shots["s01"]["fingerprint"])
    with pytest.raises(ValueError) as e:
        service.assemble(PID, width=256, height=144, with_subs=False)
    assert "s02" in str(e.value)
