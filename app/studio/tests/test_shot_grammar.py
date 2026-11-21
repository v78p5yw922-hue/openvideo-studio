from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import shot_grammar as sg  # noqa: E402


def test_camera_issues_include_field_and_allowed():
    issues = sg.camera_issues({"景别": "月球", "高度": "平视", "角度": "正面", "运镜": "固定"})
    by_field = {issue["field"]: issue for issue in issues}
    assert by_field["景别"]["allowed"] == list(sg.CAMERA_ENUMS["景别"])
    assert by_field["景别"]["slot"] == "机位"
    assert by_field["距离"]["code"] == "CAMERA_ENUM_MISSING"
    assert by_field["距离"]["allowed"] == ["0.3–10 米"]
    # 旧 validate.py 的元组契约保持不变。
    assert sg.check_camera({"景别": "月球", "高度": "平视", "角度": "正面", "运镜": "固定"}) == [
        ("CAMERA_ENUM_INVALID", "景别「月球」不在允许值：" + "/".join(sg.CAMERA_ENUMS["景别"])),
        ("CAMERA_ENUM_MISSING", "机位缺「距离」"),
    ]


def test_eyeline_allowed_uses_names_not_character_ids():
    person = {
        "name": "林婉儿", "位置": "右", "层级": "中景", "朝向": "左3-4侧",
        "视线": "看对方", "对象": "char_xxx", "占比": "50",
    }
    issues = sg.people_issues([person], ["叶一恒", "林婉儿"])
    target = next(issue for issue in issues if issue["code"] == "EYELINE_TARGET_NOT_IN_CAST")
    assert target["field"] == "对象"
    assert target["who"] == "林婉儿"
    assert target["allowed"] == ["叶一恒", "林婉儿"]
    assert "char_xxx" not in target["allowed"]


def test_relation_and_scale_allowed():
    missing = sg.relation_issues(None, 2)[0]
    assert missing["field"] == "关系" and missing["allowed"] == list(sg.RELATIONS)
    person = {"name": "甲", "位置": "左", "层级": "前景", "朝向": "面向镜头",
              "视线": "看镜头", "占比": "81"}
    scale = sg.people_issues([person], ["甲"])[0]
    assert scale["field"] == "占比" and scale["allowed"] == ["20–80%"]


def test_compose_segments_split_each_person_without_changing_separators():
    cam = {"景别": "中景", "高度": "平视", "角度": "正面", "运镜": "固定", "距离": "1.5"}
    people = [
        {"name": "甲", "位置": "左", "层级": "前景", "朝向": "面向镜头",
         "视线": "看镜头", "占比": "50"},
        {"name": "乙", "位置": "右", "层级": "中景", "朝向": "背对镜头",
         "视线": "看画外", "占比": "40"},
    ]
    segments = sg.compose_segments(cam, people, "面对面")
    assert sg.compose(cam, people, "面对面") == "".join(s["text"] for s in segments)
    assert len([s for s in segments if s["slot"] == "机位" and s["kind"] == "en"]) == 1
    assert len([s for s in segments if s["slot"] == "人物" and s["kind"] == "en"]) == 2
    assert len([s for s in segments if s["slot"] == "关系" and s["kind"] == "en"]) == 1
    assert [s["text"] for s in segments if s["kind"] == "meta"] == [". ", "; ", ". "]
    assert [s["source"] for s in segments if s["slot"] == "人物" and s["kind"] == "en"] == [
        "甲：位置=左；层级=前景；朝向=面向镜头；视线=看镜头；占比=50",
        "乙：位置=右；层级=中景；朝向=背对镜头；视线=看画外；占比=40",
    ]
