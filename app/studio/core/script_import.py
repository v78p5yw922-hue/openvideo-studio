"""Import the Markdown script format we asked DeepSeek to produce.

Deliberately tolerant: Chinese/English headings, full-width punctuation, pipes in
table cells, 「镜 1（5s）」-style numbers. Anything it cannot understand becomes a
warning instead of a silent guess.
"""

from __future__ import annotations

import re
import unicodedata

CJK_PUNCT = "：:，,、；;（）()【】[]《》"


def _slug(text: str, fallback: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"[\s《》【】()（）:：,，.。!！?？'\"“”]+", "_", s)
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "", s).strip("_")
    return s or fallback


def clean_name(name: str) -> str:
    """`Ye Qian（叶谦）` -> `叶谦`; keep plain names untouched.

    Scripts often carry "English (中文)" pairs; the library keys on the Chinese
    name, so without this every import would mint a duplicate character.
    """
    s = unicodedata.normalize("NFKC", name or "").strip()
    m = re.search(r"[（(]\s*([^）)]+?)\s*[）)]", s)
    if m:
        inner = m.group(1).strip()
        outside = (s[:m.start()] + s[m.end():]).strip(" ·-—")
        if re.search(r"[\u4e00-\u9fff]", inner) and not re.search(r"[\u4e00-\u9fff]", outside):
            return inner
        if re.search(r"[\u4e00-\u9fff]", outside) and not re.search(r"[\u4e00-\u9fff]", inner):
            return outside
    return s


def _ascii_id(text: str, prefix: str) -> str:
    """Stable id: ASCII when possible, short hash otherwise (Chinese names)."""
    import hashlib
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
    if s:
        return f"{prefix}_{s}"
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:6]
    return f"{prefix}_{digest}"


def _bullets(block: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in block.splitlines():
        m = re.match(r"\s*[-*]\s*([^：:]+)[：:]\s*(.*)$", line)
        if m:
            out[m.group(1).strip()] = m.group(2).strip()
        else:
            m2 = re.match(r"\s*\*\*([^*]+)\*\*[：:]\s*(.*)$", line)
            if m2:
                out[m2.group(1).strip()] = m2.group(2).strip()
    return out


def _pick(d: dict[str, str], *keys: str) -> str:
    for k in keys:
        for dk, dv in d.items():
            if k in dk:
                return dv
    return ""


def _sections(text: str) -> dict[str, str]:
    """Split on `## ` headings; keep order and the lowest level (### is content)."""
    out: dict[str, str] = {}
    cur = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        if re.match(r"^##\s+", line) and not re.match(r"^###\s+", line):
            out[cur] = "\n".join(buf).strip()
            cur = re.sub(r"^##\s+", "", line).strip()
            buf = []
        else:
            buf.append(line)
    out[cur] = "\n".join(buf).strip()
    return out


def _blocks(block: str, pattern: str) -> list[tuple[str, str]]:
    """Split a section into `### heading` chunks."""
    out: list[tuple[str, str]] = []
    name, buf = "", []
    for line in block.splitlines():
        m = re.match(pattern, line)
        if m:
            if name:
                out.append((name, "\n".join(buf).strip()))
            name, buf = m.group(1).strip(), []
        else:
            buf.append(line)
    if name:
        out.append((name, "\n".join(buf).strip()))
    return out


def _blocks_by_key(block: str, key: str) -> list[tuple[str, str]]:
    """Split a flat bullet list into records that each start with `key：`."""
    out: list[tuple[str, str]] = []
    name, buf = "", []
    for line in block.splitlines():
        m = re.match(rf"\s*[-*]\s*{key}\s*[：:]\s*(.*)$", line)
        if m:
            if name:
                out.append((name, "\n".join(buf).strip()))
            name, buf = m.group(1).strip(), [line]
        elif name:
            buf.append(line)
    if name:
        out.append((name, "\n".join(buf).strip()))
    return out


def _table_rows(block: str) -> list[list[str]]:
    rows = []
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows


def _shot_number(label: str, index: int) -> str:
    m = re.search(r"(\d+)", label)
    return f"s{int(m.group(1)):02d}" if m else f"s{index:02d}"


def parse(text: str) -> dict:
    secs = _sections(text)
    proj = _bullets(secs.get("项目", ""))
    world = _bullets(secs.get("世界设定", ""))

    title = _pick(proj, "片名", "title").strip("《》 ")
    style = _pick(world, "视觉语言", "风格") or _pick(proj, "视觉风格", "风格")
    duration_raw = _pick(proj, "总时长", "时长")
    m = re.search(r"(\d+)", duration_raw)
    target = int(m.group(1)) if m else 0

    characters = []
    char_blocks = _blocks(secs.get("角色卡", ""), r"^#{3,4}\s*(.+)$")
    if not char_blocks:
        char_blocks = _blocks_by_key(secs.get("角色卡", ""), "名字")
    for name, body in char_blocks:
        d = _bullets(body)
        raw_name = _pick(d, "名字") or name
        characters.append({
            "character_id": _ascii_id(clean_name(raw_name), "char"),
            "name": clean_name(raw_name),
            "description": _pick(d, "外观描述", "描述"),
            "lock": _pick(d, "一致性锁"),
            "costume": _pick(d, "服装锁"),
            "refs": [],
        })

    scenes = []
    for name, body in _blocks(secs.get("场景卡", ""), r"^#{3,4}\s*(.+)$"):
        d = _bullets(body)
        label = re.sub(r"^场景\s*\d*\s*[：:]\s*", "", name).strip()
        explicit = re.search(r"[（(]\s*id\s*[：:]\s*([A-Za-z0-9_\-]+)\s*[）)]", label)
        if explicit:
            scene_id = explicit.group(1)
            label = re.sub(r"[（(]\s*id\s*[：:].*?[）)]", "", label).strip()
        else:
            scene_id = _ascii_id(label, "scene")
        scenes.append({
            "scene_id": scene_id,
            "name": label,
            "location": _pick(d, "空间描述", "描述"),
            "camera": _pick(d, "机位"),
            "props": _pick(d, "科技物件", "固定物件", "物件"),
            "light": _pick(d, "光源", "光线"),
            "plate_shot": _pick(d, "母版镜头号"),
        })

    prompts: dict[str, str] = {}
    prompt_sec = ""
    for key in secs:
        if "prompt" in key.lower() or "提示词" in key:
            prompt_sec = secs[key]
            break
    for name, body in _blocks(prompt_sec, r"^#{3,4}\s*(.+)$"):
        sid = _shot_number(name, 0)
        prompts[sid] = body.strip()

    rows = _table_rows(secs.get("镜头表", ""))
    if not rows:
        raise ValueError("没有找到「镜头表」表格")
    header = rows[0]
    body_rows = rows[1:]
    idx = {name: i for i, name in enumerate(header)}

    def cell(row: list[str], *names: str) -> str:
        for n in names:
            for h, i in idx.items():
                if n in h and i < len(row):
                    return row[i]
        return ""

    scene_lookup = {s["name"]: s["scene_id"] for s in scenes}
    char_lookup = {c["name"]: c["character_id"] for c in characters}
    has_anchor_col = any("锚定" in h for h in header)

    shots = []
    for n, row in enumerate(body_rows, start=1):
        sid = _shot_number(cell(row, "镜号"), n)
        scene_label = cell(row, "场景")
        scene_id = scene_lookup.get(scene_label) or _ascii_id(scene_label, "scene")
        cast_raw = cell(row, "出场角色", "角色")
        cast = []
        if cast_raw and "无" not in cast_raw:
            for part in re.split(r"[、,，/；;]\s*", cast_raw):
                part = clean_name(part.strip())
                if part:
                    cast.append(char_lookup.get(part, _ascii_id(part, "char")))
        dialogue = cell(row, "台词")
        if dialogue and "无" not in dialogue:
            if "<d>" not in dialogue:
                dialogue = f"<d>{dialogue}</d>"
        else:
            dialogue = ""
        trans = cell(row, "转场") or "cut"
        if has_anchor_col:
            anchor = "plate" if "plate" in (cell(row, "锚定") or "").lower() else ""
        else:
            anchor = "plate" if not cast else ""
        shots.append({
            "shot_id": sid,
            "scene_id": scene_id,
            "cast": cast,
            "camera": cell(row, "景别", "机位"),
            "prompt": prompts.get(sid, ""),
            "dialogue": dialogue,
            "sound": "",
            "transition": "chain" if "chain" in trans.lower() else "cut",
            "anchor": anchor,
            "duration": 5.0,
        })

    warnings = []
    if not shots:
        warnings.append("镜头表是空的")
    for s in shots:
        if not s["prompt"]:
            warnings.append(f"{s['shot_id']} 没有对应的 prompt 段（会退回镜头表里的机位描述）")
    for c in characters:
        if not c["description"]:
            warnings.append(f"角色 {c['name']} 没有外观描述")

    return {
        "project": {
            "title": title or "未命名",
            "style": style,
            "target_duration": target,
        },
        "characters": characters,
        "scenes": scenes,
        "shots": shots,
        "warnings": warnings,
    }
