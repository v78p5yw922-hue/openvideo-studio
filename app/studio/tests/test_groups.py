"""项目分组与归档。"""

from __future__ import annotations

from studio.core import db, service

db.init_db()


def _make(pid: str, title: str) -> dict:
    if service.get_project(pid):
        return service.get_project(pid) or {}
    return service.create_project(pid, title)


def test_ensure_group_is_idempotent():
    g1 = service.ensure_group("测试系列A")
    g2 = service.ensure_group("测试系列A")
    assert g1["id"] == g2["id"]
    assert g1["name"] == "测试系列A"


def test_assign_and_list_group():
    _make("p_group_t1", "第一集")
    g = service.ensure_group("测试系列B")
    service.set_project_group("p_group_t1", g["id"])
    groups = {x["id"]: x for x in service.list_groups()}
    assert groups[g["id"]]["name"] == "测试系列B"
    assert any(p["id"] == "p_group_t1" and p["group_id"] == g["id"]
               for p in service.list_projects())


def test_archive_hides_from_default_list():
    _make("p_group_t2", "旧项目")
    service.set_project_archived("p_group_t2", True)
    ids = {p["id"] for p in service.list_projects()}
    assert "p_group_t2" not in ids
    ids_all = {p["id"] for p in service.list_projects(include_archived=True)}
    assert "p_group_t2" in ids_all
    service.set_project_archived("p_group_t2", False)
    assert "p_group_t2" in {p["id"] for p in service.list_projects()}


def test_delete_group_keeps_projects():
    _make("p_group_t3", "第三集")
    g = service.ensure_group("测试系列C")
    service.set_project_group("p_group_t3", g["id"])
    service.delete_group(g["id"])
    p = service.get_project("p_group_t3")
    assert p is not None
    assert p["group_id"] == ""
    assert all(x["id"] != g["id"] for x in service.list_groups())


def test_ungrouped_bucket_counts():
    bucket = [g for g in service.list_groups() if g["id"] == ""][0]
    expect = len([p for p in service.list_projects() if not p.get("group_id")])
    assert bucket["count"] == expect
