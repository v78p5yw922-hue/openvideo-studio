"""编译器重构护栏：旧输出必须逐字节保持一致，禁止更新基线迁就新实现。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import prompt_compiler as pc  # noqa: E402

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "compile_shot_baseline.json").read_text(encoding="utf-8")
)
assert len(BASELINE) >= 4
assert len({case["key"] for case in BASELINE}) == len(BASELINE)


@pytest.mark.parametrize("case", BASELINE, ids=lambda case: case["key"])
def test_compile_shot_matches_frozen_output(case):
    args = case["input"]
    actual = pc.compile_shot(
        args["shot"], args["style"], args["cast_names"], args["scene"], args["lang"],
        args["instruction"], args["cast_descriptions"],
    )
    assert actual == case["expected"]
