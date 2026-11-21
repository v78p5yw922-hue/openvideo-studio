"""测试词库覆盖：加词/删词/恢复，以及 validator 是否读到。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio.core import glossary_store as gs


@pytest.fixture()
def temp_data(tmp_path, monkeypatch):
    monkeypatch.setattr(gs.config, "data_dir", lambda: tmp_path)
    return tmp_path


def test_add_word_writes_overlay(temp_data):
    gs.add_item("action_hand", "递上去")
    data = json.loads((temp_data / "glossary-overlay.json").read_text(encoding="utf-8"))
    assert data["categories"]["action_hand"]["added"] == ["递上去"]
    merged = gs.merged()
    cat = next(c for c in merged["categories"] if c["id"] == "action_hand")
    assert "递上去" in cat["items"]


def test_remove_base_word_hides_it(temp_data):
    merged_before = gs.merged()
    cat = next(c for c in merged_before["categories"] if c["id"] == "action_hand")
    word = cat["items"][0]
    gs.remove_item("action_hand", word)
    merged_after = gs.merged()
    cat2 = next(c for c in merged_after["categories"] if c["id"] == "action_hand")
    assert word not in cat2["items"]


def test_remove_added_word_drops_it(temp_data):
    gs.add_item("action_hand", "递上去")
    gs.remove_item("action_hand", "递上去")
    cat = next(c for c in gs.merged()["categories"] if c["id"] == "action_hand")
    assert "递上去" not in cat["items"]


def test_reset_clears_overlay(temp_data):
    gs.add_item("action_hand", "递上去")
    gs.reset("")
    cat = next(c for c in gs.merged()["categories"] if c["id"] == "action_hand")
    assert "递上去" not in cat["items"]
    data = json.loads((temp_data / "glossary-overlay.json").read_text(encoding="utf-8"))
    assert data["categories"] == {}


def test_validator_sees_overlay(temp_data):
    """校验器读的是合并词库。"""
    gs.add_item("sound", "新声音词")
    from studio.core import validate as V
    V._GLOSSARY = gs.merged()
    cat = next(c for c in V._GLOSSARY["categories"] if c["id"] == "sound")
    assert "新声音词" in cat["items"]
