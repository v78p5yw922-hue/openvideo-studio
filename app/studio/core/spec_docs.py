"""规范页：解析 RULES-STRICT / REVIEW-LESSONS 成可搜索条目。

server 端：GET /api/spec
  { rules: [{n, section, text}], lessons: [{n, title, body}], sources: {...} }

文档来源：<deploy>/studio/data/spec/*.md（deploy 时从 openvideo-release/docs 复制）
"""

from __future__ import annotations

import re
from pathlib import Path


def spec_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "spec"


def _read(name: str) -> str:
    p = spec_dir() / name
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def parse_rules(text: str) -> list[dict]:
    """规则是 "N. 文本" 的行；所在 ## 段作为分组。"""
    out: list[dict] = []
    section = ""
    for raw in text.splitlines():
        line = raw.rstrip()
        m2 = re.match(r"^##\s+(.+)$", line)
        if m2:
            section = m2.group(1).strip()
            continue
        m = re.match(r"^(\d{1,3})\.\s+(.*)$", line.strip())
        if m and section:
            body = [m.group(2).strip()]
            out.append({"n": int(m.group(1)), "section": section, "text": body[0]})
            continue
        # 续行（缩进行）并入上一条
        if out and line.startswith(("   ", "\t")) and line.strip():
            out[-1]["text"] += " " + line.strip()
    return out


def parse_lessons(text: str) -> list[dict]:
    """## N. 标题 + 正文（代码块/列表都算正文）。"""
    out: list[dict] = []
    cur: dict | None = None
    for raw in text.splitlines():
        m = re.match(r"^##\s+(\d+)\.\s*(.+)$", raw.strip())
        if m:
            if cur:
                out.append(cur)
            cur = {"n": int(m.group(1)), "title": m.group(2).strip(), "body": []}
            continue
        if raw.startswith("## "):
            if cur:
                out.append(cur)
                cur = None
            continue
        if cur is not None:
            cur["body"].append(raw)
    if cur:
        out.append(cur)
    for it in out:
        it["body"] = "\n".join(it["body"]).strip()
    return out


def payload() -> dict:
    rules_text = _read("RULES-STRICT.md")
    lessons_text = _read("REVIEW-LESSONS.md")
    rules = parse_rules(rules_text)
    lessons = parse_lessons(lessons_text)
    sections: dict[str, int] = {}
    for r in rules:
        sections[r["section"]] = sections.get(r["section"], 0) + 1
    return {
        "rules": rules,
        "lessons": lessons,
        "sections": [{"name": k, "count": v} for k, v in sections.items()],
        "sources": {
            "rules": "openvideo-release/docs/RULES-STRICT.md",
            "lessons": "openvideo-release/docs/REVIEW-LESSONS.md",
            "available": bool(rules_text) and bool(lessons_text),
            "dir": str(spec_dir()),
        },
    }
