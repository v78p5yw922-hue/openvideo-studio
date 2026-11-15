"""Browser-visible self test: proves the invariants on the live database.

Runs on a throwaway project and cleans up after itself, so it is safe to hit at
any time. No GPU, no mutation of real projects.
"""

from __future__ import annotations

from ..core import service


def run() -> dict:
    checks: list[dict] = []

    def check(name: str, fn):
        try:
            detail = fn()
            checks.append({"name": name, "ok": True, "detail": detail})
        except Exception as e:  # noqa: BLE001
            checks.append({"name": name, "ok": False, "detail": str(e)})

    pid = "selftest_tmp"

    def db_ok():
        projects = service.list_projects()
        return f"{len(projects)} 个项目"

    def schema_ok():
        with service.db.txn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        names = {r["name"] for r in rows}
        need = {"projects", "shots", "takes", "reviews", "previews", "jobs", "events"}
        missing = need - names
        if missing:
            raise AssertionError(f"缺表 {missing}")
        return f"{len(names)} 张表"

    def setup():
        if service.get_project(pid):
            service.delete_project(pid)
        service.create_project(pid, "自检", "")
        service.upsert_scene(pid, "sc1", name="A")
        service.save_shots(pid, [
            {"shot_id": "s01", "scene_id": "sc1", "cast": [], "prompt": "p1", "transition": "cut"},
            {"shot_id": "s02", "scene_id": "sc1", "cast": [], "prompt": "p2", "transition": "chain"},
        ])
        return "临时项目已建"

    def fingerprint_stable():
        a = [s["fingerprint"] for s in service.recompute_fingerprints(pid)]
        b = [s["fingerprint"] for s in service.recompute_fingerprints(pid)]
        if a != b:
            raise AssertionError("同一输入两次指纹不同")
        return a[0][:12]

    def cascade_ok():
        before = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(pid)}
        shots = service.list_shots(pid)
        shots[0]["prompt"] = "changed"
        service.save_shots(pid, shots)
        after = {s["shot_id"]: s["fingerprint"] for s in service.list_shots(pid)}
        if before["s01"] == after["s01"]:
            raise AssertionError("改了输入指纹没变")
        if before["s02"] == after["s02"]:
            raise AssertionError("chain 下游没有级联失效")
        return "s01→s02 已级联"

    def take_naming_ok():
        t = service.reserve_take(pid, "s01")
        if not t["path"].endswith("s01_v1.mp4"):
            raise AssertionError(f"命名错误 {t['path']}")
        return "s01_v1.mp4"

    def plan_gate_ok():
        p1 = service.plan(pid)
        p2 = service.plan(pid)
        if p1["plan_id"] != p2["plan_id"]:
            raise AssertionError("未改动时计划 id 不稳定")
        shots = service.list_shots(pid)
        shots[1]["prompt"] = "changed again"
        service.save_shots(pid, shots)
        p3 = service.plan(pid)
        if p3["plan_id"] == p1["plan_id"]:
            raise AssertionError("改动后计划 id 没变（确认门会失效）")
        return "计划 id 随输入变化"

    def preview_not_take_ok():
        service.record_preview(pid, "s01", "/tmp/none.jpg", "fp")
        st = service.shot_state(pid, service.list_shots(pid)[0])
        if st["state"] != "empty":
            raise AssertionError("预览改变了镜头状态")
        return "预览不计入 take"

    def cleanup():
        service.delete_project(pid)
        return "临时项目已删"

    check("数据库可读", db_ok)
    check("数据表齐全", schema_ok)
    check("建立临时项目", setup)
    check("指纹对同一输入稳定", fingerprint_stable)
    check("chain 下游级联失效", cascade_ok)
    check("take 按 shot_id 命名", take_naming_ok)
    check("计划 id 与输入绑定", plan_gate_ok)
    check("预览不等于 take", preview_not_take_ok)
    check("清理临时项目", cleanup)

    passed = sum(1 for c in checks if c["ok"])
    return {"passed": passed, "total": len(checks), "checks": checks}
