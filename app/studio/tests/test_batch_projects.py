"""批量操作的后端测试。"""

from __future__ import annotations

from studio.core import db, service

db.init_db()


def _mk(pid: str, title: str) -> None:
    if not service.get_project(pid):
        service.create_project(pid, title)


def test_batch_move_then_archive():
    for i in range(3):
        _mk(f"p_batch_{i}", f"批量测试 {i}")
    ids = [f"p_batch_{i}" for i in range(3)]
    g = service.ensure_group("批量系列")

    r = service.batch_projects(ids, "group", g["id"])
    assert r["count"] == 3
    assert all(service.get_project(i)["group_id"] == g["id"] for i in ids)

    r = service.batch_projects(ids, "archive")
    assert r["count"] == 3
    listed = {p["id"] for p in service.list_projects()}
    assert not (set(ids) & listed)
    assert set(ids) <= {p["id"] for p in service.list_projects(include_archived=True)}

    service.batch_projects(ids, "unarchive")
    assert set(ids) <= {p["id"] for p in service.list_projects()}


def test_batch_rejects_unknown_action():
    _mk("p_batch_x", "未知动作")
    try:
        service.batch_projects(["p_batch_x"], "explode")
    except ValueError as e:
        assert "未知批量操作" in str(e)
    else:
        raise AssertionError("应该抛 ValueError")


def test_batch_empty_is_noop():
    assert service.batch_projects([], "archive")["count"] == 0
