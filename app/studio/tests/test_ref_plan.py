"""槽位预算的单元测试（不含渲染，纯算账）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from studio.core import refs  # noqa: E402


def _member(name: str, n: int, facing: str = "面向镜头", share: float = 30.0) -> dict:
    return {"character_id": f"c_{name}", "name": name,
            "refs": [f"/tmp/{name}_{i}.png" for i in range(n)],
            "facing": facing, "share": share}


def _shot(size: str, chain: bool = False) -> dict:
    return {"shot_id": "s01", "camera_slots": {"景别": size}, "chain_ref": int(chain)}


def _scene(n: int = 2) -> dict:
    return {"plate_path": "/tmp/plate.png",
            "extra_plates": [f"/tmp/alt{i}.png" for i in range(1, n)]}


def test_three_members_keep_scene_and_frame():
    plan = refs.plan_refs(_shot("中全", chain=True),
                          [_member("妈妈", 3, share=42), _member("小石头", 3, share=30),
                           _member("大黄", 3, share=18)],
                          _scene(2))
    usage = plan["usage"]
    assert usage["total"] <= 9
    assert usage["scene"] >= 1, "场景图必须留位（这是本模块存在的理由）"
    assert usage["frame"] == 1, "开了末帧就要留它一位"
    assert usage["cast"] + usage["scene"] + usage["frame"] == usage["total"]


def test_three_members_each_get_two_views_at_most():
    plan = refs.plan_refs(_shot("中全", chain=True),
                          [_member("A", 4), _member("B", 4), _member("C", 4)], _scene(2))
    per_owner: dict[str, int] = {}
    for s in plan["slots"]:
        if s["kind"] == "cast":
            per_owner[s["owner"]] = per_owner.get(s["owner"], 0) + 1
    assert len(per_owner) == 3, "三个人都要有图（不能有人被跳过）"
    assert max(per_owner.values()) <= 2, "三人同框每人最多 2 张"


def test_closeup_drops_scene_on_purpose():
    plan = refs.plan_refs(_shot("特写"), [_member("妈妈", 4)], _scene(2))
    assert plan["usage"]["scene"] == 0, "特写背景虚化，场景图不值得占槽"


def test_two_members_get_three_views_each():
    plan = refs.plan_refs(_shot("中景"), [_member("A", 4), _member("B", 4)], _scene(2))
    per_owner: dict[str, int] = {}
    for s in plan["slots"]:
        if s["kind"] == "cast":
            per_owner[s["owner"]] = per_owner.get(s["owner"], 0) + 1
    assert per_owner["A"] >= 3 and per_owner["B"] >= 3


def test_single_member_gets_all_four_views():
    plan = refs.plan_refs(_shot("近景"), [_member("A", 4)], _scene(2))
    assert sum(1 for s in plan["slots"] if s["kind"] == "cast") == 4


def test_total_never_exceeds_cap():
    plan = refs.plan_refs(_shot("全景", chain=True),
                          [_member("A", 4), _member("B", 4), _member("C", 4)],
                          _scene(4))
    assert plan["usage"]["total"] <= 9
    assert all(s["index"] == i + 1 for i, s in enumerate(plan["slots"]))


def test_back_view_preferred_when_facing_away():
    m = _member("A", 3)
    m["ref_views"] = ["front", "side", "back"]
    m["facing"] = "背对镜头"
    plan = refs.plan_refs(_shot("中景"), [m], None)
    assert plan["slots"][0]["path"].endswith("A_2.png"), "背对镜头时先给背面图"


def test_scene_only_shot_still_uses_plate():
    plan = refs.plan_refs(_shot("全景"), [], _scene(2))
    assert plan["usage"]["scene"] == 2
    assert plan["usage"]["cast"] == 0


def test_signature_changes_when_refs_change():
    a = refs.plan_refs(_shot("中景"), [_member("A", 3)], _scene(1))
    b = refs.plan_refs(_shot("中景"), [_member("A", 3)], _scene(1))
    assert refs.plan_signature(a) == refs.plan_signature(b)
    c = refs.plan_refs(_shot("中景"), [_member("A", 2)], _scene(1))
    assert refs.plan_signature(a) != refs.plan_signature(c)
