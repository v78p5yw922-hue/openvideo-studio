"""Core invariants: identity, fingerprint, cascade, state, plan, confirm.

These are the tests the review asked for: the ones that catch a platform which
"looks successful but ships old pixels".
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_DATA"] = TMP
os.environ["STUDIO_ALLOW_RENDER"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # app/

from studio.core import db, model, service  # noqa: E402

db.init_db()


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "jobs", "events", "scenes", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def _project(pid="p1"):
    service.create_project(pid, "测试片", "cinematic")
    return pid


def _shots(pid, n=3, chain_from=2):
    shots = []
    for i in range(1, n + 1):
        shots.append({
            "shot_id": f"s{i:02d}", "scene_id": "sc1",
            "cast": ["c1"] if i > 1 else [],
            "camera": "medium", "prompt": f"prompt {i}", "dialogue": "",
            "transition": "chain" if i >= chain_from else "cut",
        })
    service.save_shots(pid, shots)
    return service.list_shots(pid)


def test_fingerprint_tracks_scene_plate(tmp_path):
    pid = _project()
    plate = tmp_path / "plate.jpg"
    plate.write_bytes(b"AAA")
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    shots = _shots(pid)
    before = {s["shot_id"]: s["fingerprint"] for s in shots}

    plate.write_bytes(b"BBB")                      # plate content changed
    service.upsert_scene(pid, "sc1", name="A", plate_path=str(plate))
    after = {s["shot_id"]: s["fingerprint"] for s in service.recompute_fingerprints(pid)}

    assert all(before[k] != after[k] for k in before), "换母版必须让所有镜头失效"


def test_chain_cascade_stops_at_cut():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=4, chain_from=2)   # s1 cut, s2 chain, s3 chain, s4 chain
    assert [s["transition"] for s in shots] == ["cut", "chain", "chain", "chain"]
    before = {s["shot_id"]: s["fingerprint"] for s in shots}

    shots[0]["prompt"] = "changed first shot"          # s01 changed (cut)
    service.save_shots(pid, shots)
    after = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(pid)}

    assert before["s01"] != after["s01"]
    # s02 is a chain of s01 -> must cascade; then s03 chains s02, s04 chains s03
    assert before["s02"] != after["s02"], "chain 下游必须级联失效"
    assert before["s03"] != after["s03"]
    assert before["s04"] != after["s04"]


def test_cut_isolates_cascade():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=3, chain_from=3)   # s1 cut, s2 cut, s3 chain(of s2)
    assert [s["transition"] for s in shots] == ["cut", "cut", "chain"]
    base = {s["shot_id"]: s["fingerprint"] for s in shots}

    # changing a `cut` shot must not touch its successor
    shots[0]["prompt"] = "changed s01"
    service.save_shots(pid, shots)
    after = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(pid)}
    assert base["s01"] != after["s01"]
    assert base["s02"] == after["s02"], "cut 不应向下游传播"
    assert base["s03"] == after["s03"]

    # changing the shot a chain depends on must invalidate the chain
    shots[1]["prompt"] = "changed s02"
    service.save_shots(pid, shots)
    final = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(pid)}
    assert final["s02"] != after["s02"]
    assert final["s03"] != after["s03"], "chain 镜头必须跟着上游失效"


def test_state_lifecycle():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid)
    sid = shots[0]["shot_id"]
    assert service.shot_state(pid, shots[0])["state"] == "empty"

    service.record_take(pid, sid, "/tmp/x.mp4", shots[0]["fingerprint"], 500.0, 24000)
    shots = service.list_shots(pid)
    assert service.shot_state(pid, shots[0])["state"] == "review"

    service.review_shot(pid, sid, "pass")
    shots = service.list_shots(pid)
    assert service.shot_state(pid, shots[0])["state"] == "passed"

    shots[0]["prompt"] = "new prompt"
    service.save_shots(pid, shots)
    shots = service.list_shots(pid)
    assert service.shot_state(pid, shots[0])["state"] == "stale", "改输入后必须变 stale"


def test_reject_then_rerender_bumps_version():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid)
    sid = shots[0]["shot_id"]
    fp = shots[0]["fingerprint"]
    service.record_take(pid, sid, "/tmp/a.mp4", fp)
    service.review_shot(pid, sid, "reject", "构图")
    assert service.shot_state(pid, service.list_shots(pid)[0])["state"] == "rejected"
    second = service.record_take(pid, sid, "/tmp/b.mp4", fp)
    assert second["version"] == 2
    assert service.shot_state(pid, service.list_shots(pid)[0])["state"] == "review"


def test_plan_counts_warns_and_blocks():
    pid = _project()
    shots = [
        {"shot_id": "s01", "scene_id": "sc_missing", "cast": [], "transition": "cut",
         "prompt": "p", "dialogue": "", "anchor": "plate"},
        {"shot_id": "s02", "scene_id": "sc_missing", "cast": ["a", "b", "c", "d"],
         "transition": "cut", "prompt": "p", "dialogue": ""},
        {"shot_id": "s03", "scene_id": "sc_missing", "cast": ["a"], "transition": "cut",
         "prompt": "p", "dialogue": "<d>林澈：" + "字" * 40 + "</d>"},
    ]
    service.save_shots(pid, shots)
    p = service.plan(pid)
    assert p["count"] == 3
    assert p["est_minutes"] == pytest.approx(3 * 8.5, abs=0.2)
    msgs = " ".join(w["msg"] for w in p["warnings"])
    assert "母版" in msgs
    assert "上限 3" in msgs
    assert "读不完" in msgs
    assert p["blocking"], "缺母版 + 4 个角色应产生阻断"


def test_plan_skips_passed_shots():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=2, chain_from=2)
    sid = shots[0]["shot_id"]
    service.record_take(pid, sid, "/tmp/a.mp4", shots[0]["fingerprint"])
    service.review_shot(pid, sid, "pass")
    p = service.plan(pid, draft=False)
    assert sid not in [s["shot_id"] for s in p["shots"]]


def test_plan_blocks_chain_without_upstream_take():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=3, chain_from=2)   # s2 chain of s1, s3 chain of s2
    p = service.plan(pid, shot_ids=["s02"], draft=False)
    assert any(w["level"] == "error" and "上游" in w["msg"] for w in p["warnings"]), p["warnings"]


def test_plan_ok_when_upstream_is_in_the_same_plan():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    _shots(pid, n=2, chain_from=2)
    p = service.plan(pid)          # both shots included, s01 first
    assert not [w for w in p["warnings"] if "上游" in w["msg"]]


def test_plan_single_chain_shot_uses_project_order():
    """Planning one chain shot must look at its real predecessor, not the subset."""
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=3, chain_from=2)   # s1 cut, s2 chain, s3 chain
    service.record_take(pid, "s01", "/tmp/a.mp4", shots[0]["fingerprint"])
    p = service.plan(pid, shot_ids=["s02"], draft=False)
    assert not [w for w in p["warnings"] if "第一镜" in w["msg"]], p["warnings"]
    assert not p["blocking"], p["blocking"]


def test_delete_take_removes_file_and_reverts_state(tmp_path):
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=1, chain_from=99)
    f = tmp_path / "take.mp4"
    f.write_bytes(b"x" * 100)
    service.record_take(pid, "s01", str(f), shots[0]["fingerprint"])
    assert service.shot_state(pid, service.list_shots(pid)[0])["state"] == "review"

    r = service.delete_take(pid, "s01")
    assert r["deleted_version"] == 1 and not r["remaining"]
    assert not f.exists(), "文件也要删"
    assert service.shot_state(pid, service.list_shots(pid)[0])["state"] == "empty"


def test_delete_older_version_keeps_newest():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    shots = _shots(pid, n=1, chain_from=99)
    service.record_take(pid, "s01", "/tmp/a1.mp4", shots[0]["fingerprint"])
    service.record_take(pid, "s01", "/tmp/a2.mp4", shots[0]["fingerprint"])
    r = service.delete_take(pid, "s01", version=1)
    assert r["remaining"] == [2]
    assert service.list_shots(pid)[0]["shot_id"] == "s01"


def test_plan_id_changes_when_inputs_change():
    pid = _project()
    service.upsert_scene(pid, "sc1", name="A")
    _shots(pid)
    p1 = service.plan(pid)
    shots = service.list_shots(pid)
    shots[0]["prompt"] = "different"
    service.save_shots(pid, shots)
    p2 = service.plan(pid)
    assert p1["plan_id"] != p2["plan_id"], "改了输入，旧计划必须作废"
