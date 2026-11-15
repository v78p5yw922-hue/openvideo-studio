"""Last-frame endpoint: the image a chain successor starts from must be visible."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-lf-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402

db.init_db()
client = TestClient(app)
PID = "lf_test"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    service.create_project(PID, "尾帧测试", "")
    service.upsert_scene(PID, "sc1", name="A")
    service.save_shots(PID, [
        {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "a", "transition": "cut"},
        {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "b", "transition": "chain"},
    ])
    yield


def _make_clip(path: Path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=0x3366aa:s=96x54:d=1", "-pix_fmt", "yuv420p", str(path)],
                   check=False)


def test_lastframe_404_without_take():
    r = client.get(f"/assets/lastframe/{PID}/s01")
    assert r.status_code == 404


def test_lastframe_extracts_and_caches(tmp_path):
    clip = tmp_path / "s01_v1.mp4"
    _make_clip(clip)
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, "s01", str(clip), shots["s01"]["fingerprint"])

    r = client.get(f"/assets/lastframe/{PID}/s01")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    cache = clip.with_name("s01_v1_last.jpg")
    assert cache.is_file(), "尾帧要落盘，方便回看"
    first = r.content

    r2 = client.get(f"/assets/lastframe/{PID}/s01")
    assert r2.content == first, "缓存命中应返回同一张"


def test_lastframe_specific_version(tmp_path):
    clip1 = tmp_path / "s01_v1.mp4"
    clip2 = tmp_path / "s01_v2.mp4"
    _make_clip(clip1)
    _make_clip(clip2)
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    service.record_take(PID, "s01", str(clip1), shots["s01"]["fingerprint"])
    service.record_take(PID, "s01", str(clip2), shots["s01"]["fingerprint"])
    r = client.get(f"/assets/lastframe/{PID}/s01?version=1")
    assert r.status_code == 200
