"""H3 三字段编译器的单元测试（不碰 GPU、不碰数据库）。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]      # .../video-agent
sys.path.insert(0, str(REPO / "scripts"))
spec = importlib.util.spec_from_file_location("prompt_compiler", REPO / "scripts/prompt_compiler.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)


SHOT = {
    "visual_prompt": (
        "【机位】近景平视，距人物 1.2 米，缓慢前推。"
        "【人物】叶一恒，位于画面中央，占画幅 75%。"
        "【动作】抬头看向镜头。"
        "【场景】木格窗在左侧透入暖光，书架在右后虚化。"
        "【画质】35mm 胶片质感，浅景深。"
    ),
    "camera": "近景平视",
    "dialogue": "<d>叶一恒：这屋子有人住过。</d>",
    "soundscape": "窗外的风掠过竹叶，木地板轻响。",
    "music": "低音提琴长音，速度缓慢，带一点悲伤感",
    "characters_present": ["叶一恒"],
}


def test_three_fields_present_and_ordered():
    out = pc.compile_shot(SHOT, "cinematic, soft daylight")
    assert out.count("integrated_multimodal_description:") == 1
    assert out.count("overall_soundscape:") == 1
    assert out.count("non_diegetic_music:") == 1
    i = out.index("integrated_multimodal_description:")
    s = out.index("overall_soundscape:")
    m = out.index("non_diegetic_music:")
    assert i < s < m


def test_style_first_and_shot_bracket():
    desc = pc.compile_shot(SHOT, "cinematic").split("\n\n")[0]
    assert desc.startswith("integrated_multimodal_description: [Shot 1]")
    assert "cinematic" in desc


def test_slots_are_flattened_no_brackets_left():
    desc = pc.compile_shot(SHOT, "cinematic").split("\n\n")[0]
    assert "【" not in desc and "】" not in desc
    assert "抬头看向镜头" in desc


def test_dialogue_gets_speaker_id_and_lang_tag():
    desc = pc.compile_shot(SHOT, "cinematic").split("\n\n")[0]
    assert "(S1)" in desc
    assert "<d>[中文] 这屋子有人住过。</d>" in desc


def test_music_mood_words_stripped():
    out = pc.compile_shot(SHOT, "cinematic")
    music_line = [l for l in out.splitlines() if l.startswith("non_diegetic_music:")][0]
    assert "悲伤" not in music_line
    assert "低音提琴" in music_line


def test_empty_music_falls_back_to_na():
    shot = dict(SHOT, music="")
    out = pc.compile_shot(shot, "cinematic")
    assert "non_diegetic_music: N/A" in out


def test_empty_sound_falls_back_not_empty():
    shot = dict(SHOT, soundscape="")
    out = pc.compile_shot(shot, "cinematic")
    line = [l for l in out.splitlines() if l.startswith("overall_soundscape:")][0]
    assert len(line) > len("overall_soundscape: ")


def test_i2v_instruction_is_prepended():
    out = pc.compile_shot(SHOT, "cinematic", instruction=pc.i2v_instruction())
    assert out.startswith("For the target video, at 0.00 seconds")
    assert "integrated_multimodal_description:" in out


def test_plain_prompt_without_slots_still_works():
    shot = {"visual_prompt": "A young man looks up from a book.", "camera": "medium shot",
            "dialogue": "", "soundscape": "", "music": "", "characters_present": []}
    out = pc.compile_shot(shot, "cinematic")
    assert "A young man looks up from a book." in out
    assert "integrated_multimodal_description:" in out


def test_no_duplicate_dialogue_in_soundscape():
    out = pc.compile_shot(SHOT, "cinematic")
    sound_line = [l for l in out.splitlines() if l.startswith("overall_soundscape:")][0]
    assert "这屋子有人住过" not in sound_line


def structured_shot():
    return {
        "visual_prompt": (
            "【机位】景别=中景；高度=平视；角度=正面；运镜=固定；距离=1.5\n"
            "【人物】- 叶一恒：位置=左；层级=前景；朝向=面向镜头；视线=看对方；对象=林婉儿；占比=50\n"
            "- 林婉儿：位置=右；层级=中景；朝向=背对镜头；视线=看镜头；占比=40\n"
            "【关系】面对面\n"
            "【动作】这段动作被替代\n"
            "【节奏】0.0 站定 | 2.0 切近景，拱手\n"
            "【场景】庙会街"
        ),
        "camera": "", "dialogue": "", "soundscape": "", "music": "",
        "characters_present": ["叶一恒", "林婉儿"],
    }


def test_report_segments_recompose_and_split_spatial_and_beats():
    shot = structured_shot()
    report = pc.compile_shot_report(shot, "cinematic")
    assert report["compiled"] == pc.compile_shot(shot, "cinematic")
    assert report["compiled"] == "".join(segment["text"] for segment in report["segments"])
    segments = report["segments"]
    assert len([s for s in segments if s["slot"] == "机位" and s["kind"] == "en"]) == 1
    assert len([s for s in segments if s["slot"] == "人物" and s["kind"] == "en"]) == 2
    assert len([s for s in segments if s["slot"] == "关系" and s["kind"] == "en"]) == 1
    assert any(s["kind"] == "meta" and s["text"] == "; " for s in segments)
    assert sum(s["kind"] == "meta" and s["text"] == ". " for s in segments) == 2
    cut = next(s for s in segments if s["slot"] == "节奏" and s["kind"] == "en")
    assert cut["text"] == "the camera cuts to "
    assert any(s["slot"] == "节奏" and s["kind"] == "zh" and s["text"] == "站定"
               for s in segments)
    assert any(s["slot"] == "节奏" and s["kind"] == "zh" and s["text"] == "近景，拱手"
               for s in segments)
    assert report["dropped"] == [
        {"slot": "动作", "source": "这段动作被替代", "reason": "已用【节奏】替代",
         "replaced_by": "节奏"}
    ]


def test_report_dropped_covers_timeline_open_unknown_and_overrides():
    shot = {
        "visual_prompt": (
            "【机位】中景平视\n【人物】阿甲\n【关系】面对面\n【动作】抬手\n"
            "【节奏】0.0 站定\n【时间轴】1.0 不应采用\n【开口】1.2\n"
            "【未知】没有编译规则\n【声音】槽内风声\n【配乐】槽内琴声\n【场景】旧屋"
        ),
        "dialogue": "", "soundscape": "镜头表风声", "music": "镜头表琴声",
        "characters_present": ["阿甲"],
    }
    report = pc.compile_shot_report(shot)
    dropped = {item["slot"]: item for item in report["dropped"]}
    assert dropped["动作"]["replaced_by"] == "节奏"
    assert dropped["时间轴"]["reason"] == "已用【节奏】替代"
    assert dropped["开口"]["reason"] == "没有台词可调度"
    assert dropped["关系"]["reason"] == "自由文本路径不使用【关系】"
    assert dropped["未知"]["reason"] == "未识别的槽位，未参与编译"
    assert dropped["声音"]["replaced_by"] == "音效"
    assert dropped["配乐"]["replaced_by"] == "配乐"
    assert "镜头表风声" in report["compiled"] and "槽内风声" not in report["compiled"]
    assert "镜头表琴声" in report["compiled"] and "槽内琴声" not in report["compiled"]
    assert report["compiled"] == "".join(s["text"] for s in report["segments"])


def test_report_errors_expose_names_in_allowed_values():
    shot = structured_shot()
    shot["visual_prompt"] = shot["visual_prompt"].replace("对象=林婉儿", "对象=char_xxx")
    report = pc.compile_shot_report(shot)
    target = next(e for e in report["errors"] if e["code"] == "EYELINE_TARGET_NOT_IN_CAST")
    assert target["field"] == "对象" and target["who"] == "叶一恒"
    assert target["allowed"] == ["叶一恒", "林婉儿"]
    assert "char_xxx" not in target["allowed"]
