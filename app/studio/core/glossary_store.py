"""词库存储：基础词库 + 用户覆盖（不改仓库文件）。

基础：<repo>/studio/data/script-glossary.json（随代码走）
覆盖：<data_dir>/glossary-overlay.json（用户改的，不进版本库）

覆盖结构：
    {
      "categories": {
        "action_hand": {"added": ["递上去"], "removed": ["递给"]},
        "prop_state":  {"added": ["焦边"]}
      }
    }

这样 deploy 覆盖 studio/ 时不会把用户加的词冲掉。
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import config

BASE = Path(__file__).resolve().parents[1] / "data" / "script-glossary.json"


def overlay_path() -> Path:
    return config.data_dir() / "glossary-overlay.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def load_overlay() -> dict:
    data = _read_json(overlay_path(), {}) or {}
    cats = data.get("categories")
    return {"categories": cats if isinstance(cats, dict) else {}}


def save_overlay(overlay: dict) -> None:
    p = overlay_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(overlay, ensure_ascii=False, indent=2), encoding="utf-8")


def merged() -> dict:
    """基础词库 + 覆盖，返回与 script-glossary.json 同结构。"""
    base = _read_json(BASE, {}) or {}
    ov = load_overlay()["categories"]
    for cat in base.get("categories") or []:
        cid = cat.get("id")
        items = list(cat.get("items") or [])
        rule = ov.get(cid) or {}
        removed = set(rule.get("removed") or [])
        items = [i for i in items if i not in removed]
        for extra in rule.get("added") or []:
            if extra and extra not in items:
                items.append(extra)
        cat["items"] = items
        cat["_added"] = [i for i in (rule.get("added") or []) if i in items]
        cat["_removed"] = sorted(removed)
    base["overlay_path"] = str(overlay_path())
    return base


def add_item(category_id: str, item: str) -> dict:
    item = (item or "").strip()
    if not item:
        raise ValueError("词条不能为空")
    ov = load_overlay()
    rule = ov["categories"].setdefault(category_id, {})
    added = [x for x in (rule.get("added") or []) if x]
    removed = [x for x in (rule.get("removed") or []) if x]
    if item in removed:                       # 之前隐藏过 → 取消隐藏
        removed.remove(item)
    elif item not in added:
        added.append(item)
    rule["added"] = added
    rule["removed"] = removed
    save_overlay(ov)
    return ov


def remove_item(category_id: str, item: str) -> dict:
    item = (item or "").strip()
    ov = load_overlay()
    rule = ov["categories"].setdefault(category_id, {})
    added = [x for x in (rule.get("added") or []) if x and x != item]
    removed = [x for x in (rule.get("removed") or []) if x]
    base = _read_json(BASE, {}) or {}
    in_base = any(item in (c.get("items") or []) for c in base.get("categories") or []
                  if c.get("id") == category_id)
    if in_base and item not in removed:
        removed.append(item)                  # 基础词 → 记为隐藏
    rule["added"] = added
    rule["removed"] = removed
    save_overlay(ov)
    return ov


def reset(category_id: str = "") -> dict:
    ov = load_overlay()
    if category_id:
        ov["categories"].pop(category_id, None)
    else:
        ov["categories"] = {}
    save_overlay(ov)
    return ov
