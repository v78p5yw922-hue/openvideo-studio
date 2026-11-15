"""Multiple character reference views: stored, served, and actually used."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-refs-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from fastapi.testclient import TestClient  # noqa: E402

from studio.api.app import app  # noqa: E402
from studio.core import db, service  # noqa: E402
from studio.exec import render_adapter  # noqa: E402

db.init_db()
client = TestClient(app)
CID = "multi_ref"


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _img(name: str, data: bytes) -> Path:
    p = Path(TMP) / name
    p.write_bytes(b"\xff\xd8" + data)
    return p


def test_three_refs_are_kept_and_ordered():
    a, b, c = (_img(f"{n}.jpg", bytes([i]) * 50) for i, n in enumerate("abc", start=1))
    service.upsert_character(CID, "甲", "desc", [str(a)])
    for extra in (b, c):
        cur = {x["character_id"]: x for x in service.list_characters()}[CID]
        refs = [str(extra)] + cur["refs"]
        service.upsert_character(CID, "甲", "desc", refs)
    cur = {x["character_id"]: x for x in service.list_characters()}[CID]
    assert len(cur["refs"]) == 3
    assert cur["refs"][0] == str(c), "最新添加的成为主图"
    assert cur["primary_ref"] == str(c)


def test_character_detail_and_ref_asset():
    a, b = _img("x.jpg", b"1" * 40), _img("y.jpg", b"2" * 40)
    service.upsert_character(CID, "甲", "desc", [str(a), str(b)])
    d = client.get(f"/api/characters/{CID}").json()
    assert len(d["refs"]) == 2
    assert client.get(f"/assets/characters/{CID}/ref/0").status_code == 200
    assert client.get(f"/assets/characters/{CID}/ref/1").status_code == 200
    assert client.get(f"/assets/characters/{CID}/ref/9").status_code == 404


def test_set_primary_and_delete():
    a, b = _img("p.jpg", b"3" * 40), _img("q.jpg", b"4" * 40)
    service.upsert_character(CID, "甲", "desc", [str(a), str(b)])
    r = client.post(f"/api/characters/{CID}/ref/primary", json={"path": str(b)})
    assert r.status_code == 200 and r.json()["primary_ref"] == str(b)

    r2 = client.delete(f"/api/characters/{CID}/ref", params={"path": str(a)})
    assert r2.status_code == 200
    d = client.get(f"/api/characters/{CID}").json()
    assert d["refs"] == [str(b)]


def test_workflow_uses_every_reference_and_sets_max_views():
    pid = "refs_proj"
    service.create_project(pid, "参考图测试", "")
    a, b, c = (_img(f"r{i}.jpg", bytes([i]) * 40) for i in range(1, 4))
    service.upsert_character(CID, "甲", "desc", [str(a), str(b), str(c)])
    service.upsert_scene(pid, "sc1", name="A")
    service.save_shots(pid, [{"shot_id": "s01", "scene_id": "sc1", "cast": [CID],
                              "prompt": "p", "transition": "cut"}])
    script = render_adapter.build_script(pid)
    shot = script["shots"][0]
    assert shot["characters_present"] == ["甲"]

    sys.path.insert(0, str(Path.home() / "openvideo/video-agent/scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "rd2", Path.home() / "openvideo/video-agent/scripts/render_drama.py")
    rd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rd)
    chars = {"甲": {"name": "甲", "description": "", "reference_images": [str(a), str(b), str(c)]}}
    wf = rd.build_workflow({"shot_id": "s01", "characters_present": ["甲"],
                            "visual_prompt": "p"}, chars, "", None, 1344, 768, 12, 42)
    member = wf["cast_member_1"]["inputs"]
    assert member.get("image_1") and member.get("image_2") and member.get("image_3")
    assert wf["cast_to_video"]["inputs"]["max_views_per_member"] == 3, \
        "多张参考图必须真的被使用"
