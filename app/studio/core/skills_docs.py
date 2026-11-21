"""技能（skill）列表：读 studio/data/skills/<name>/SKILL.md。

技能就是一份可直接交给别的模型的指令文件（frontmatter + 正文）。
这个接口不做执行，只负责"把文件给出去"。
"""

from __future__ import annotations

import re
from pathlib import Path


def skills_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "skills"


def _split_front(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m.group(2)


def payload() -> dict:
    root = skills_dir()
    out = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            f = d / "SKILL.md" if d.is_dir() else d
            if not f.is_file():
                continue
            raw = f.read_text(encoding="utf-8")
            meta, body = _split_front(raw)
            out.append({
                "name": meta.get("name") or d.name,
                "description": meta.get("description") or "",
                "path": str(f),
                "body": body.strip(),
                "chars": len(body),
            })
    return {"skills": out, "dir": str(root), "available": bool(out)}
