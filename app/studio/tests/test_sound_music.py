"""镜头表的「音效 / 配乐」两列必须被解析并落库。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

TMP = tempfile.mkdtemp(prefix="studio-snd-")
os.environ.setdefault("STUDIO_DATA", TMP)
os.environ["STUDIO_DISABLE_EXECUTOR"] = "1"
sys.path.insert(0, str(Path.home() / "openvideo/video-agent"))

from studio.core import db, script_import, service  # noqa: E402

db.init_db()
PID = "sound_test"

MD = """## 项目
- 片名：声音测试
- 总时长：10 秒（共 2 镜 × 5 秒）

## 世界设定
- 视觉语言：cinematic, soft daylight

## 场景卡
### 场景 1：书房（id：library）
- 机位：平视 3 米
- 固定物件：窗左、桌中
- 光源：左窗
- 母版镜头号：镜 1

## 镜头表
| 镜号 | 场景 | 出场角色 | 景别/机位 | 台词 | 音效 | 配乐 | 转场 | 锚定 |
|---|---|---|---|---|---|---|---|---|
| 镜 1（5s） | 书房 | 无（空镜） | 中景平视 | 无 | 窗外的风掠过竹叶，木地板轻响 | 无 | cut | plate |
| 镜 2（5s） | 书房 | 阿甲 | 近景平视 | <d>阿甲：有人吗</d> | 书页翻动，呼吸声 | 低音提琴长音，速度缓慢 | cut | 空 |

## 每镜 prompt
### 镜 1
【机位】中景平视
【人物】无
【动作】树影移动
【场景】窗左、桌中
【画质】35mm

### 镜 2
【机位】近景平视
【人物】阿甲 | 画面中 | 60%
【动作】抬头
【场景】窗左、桌中
【画质】35mm
"""


@pytest.fixture(autouse=True)
def clean():
    with db.txn() as conn:
        for t in ("shots", "takes", "reviews", "previews", "jobs", "events",
                  "scenes", "asset_versions", "projects", "characters"):
            conn.execute(f"DELETE FROM {t}")
    yield


def test_parse_reads_sound_and_music():
    r = script_import.parse(MD)
    s1, s2 = r["shots"]
    assert "竹叶" in s1["sound"]
    assert s1["music"] == ""          # 「无」应被清成空
    assert "书页" in s2["sound"]
    assert "低音提琴" in s2["music"]


def test_import_persists_sound_and_music():
    service.create_project(PID, "声音测试", "")
    service.import_markdown(PID, MD)
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    assert "呼吸声" in shots["s02"]["sound"]
    assert "低音提琴" in shots["s02"]["music"]


def test_markdown_export_roundtrip_keeps_columns():
    from studio.export import markdown as md_export
    service.create_project(PID, "声音测试", "")
    service.import_markdown(PID, MD)
    out = md_export.build_markdown(PID)
    assert "音效" in out and "配乐" in out
    assert "低音提琴" in out
    # 再导入一次，配乐不能丢
    service.import_markdown(PID, out)
    shots = {s["shot_id"]: s for s in service.list_shots(PID)}
    assert "低音提琴" in shots["s02"]["music"]
