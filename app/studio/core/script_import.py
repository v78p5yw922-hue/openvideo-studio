"""Import the Markdown script format we asked DeepSeek to produce.

Deliberately tolerant: Chinese/English headings, full-width punctuation, pipes in
table cells, 「镜 1（5s）」-style numbers. Anything it cannot understand becomes a
warning instead of a silent guess.
"""

from __future__ import annotations

import re
import unicodedata

CJK_PUNCT = "：:，,、；;（）()【】[]《》"


def _clean_md(text: str) -> str:
    """去掉 markdown 强调符号（**粗体** / `代码`）：不然会原样进提示词。"""
    return re.sub(r"\*\*|__|`", "", text or "").strip()


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


# 章节名别名：AI 写"## 一、角色卡"、"# 人物"、"## 分镜表" 都要能认。
# 顺序有讲究：先匹配更具体的（每镜 prompt > 镜头表 > 场景卡 > 角色卡）。
SECTION_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("每镜prompt", ("每镜", "prompt", "提示词", "逐镜")),
    ("镜头表", ("镜头表", "分镜表", "镜头清单", "分镜")),
    ("场景卡", ("场景卡", "场景设定", "场景")),
    ("角色卡", ("角色卡", "人物卡", "角色设定", "角色", "人物")),
    ("世界设定", ("世界设定", "视觉设定", "视觉语言", "美术", "风格")),
    ("项目", ("项目", "片名", "基本信息")),
]


def _canon_section(raw: str) -> str:
    """把任意章节标题归一到规范名；认不出就原样返回。"""
    name = re.sub(r"^[\s\d一二三四五六七八九十]+[、.．)）]?\s*", "", raw).strip()
    low = name.lower()
    for canon, keys in SECTION_ALIASES:
        for k in keys:
            if k.lower() in low:
                return canon
    return name


def _sections(text: str) -> dict[str, str]:
    """按 1–2 级标题切章节；`###` 属于内容（角色卡/场景卡的条目）。

    兼容：`# 项目`、`## 一、角色卡`、`## 分镜表`、`## 每镜 Prompt`。
    """
    out: dict[str, str] = {}
    cur = "_preamble"
    buf: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,2}\s+", line) and not re.match(r"^###\s+", line):
            out[cur] = "\n".join(buf).strip()
            cur = _canon_section(re.sub(r"^#{1,2}\s+", "", line).strip())
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


def _char_blocks(section: str) -> list[tuple[str, str]]:
    """角色卡的三种写法都要能吃：

    1. `### 叶一恒` + 子弹点   （严格模板）
    2. `- 名字：叶一恒`        （子弹点键值）
    3. `- 叶一恒：` 空值开头   （大模型最常写的形式）
    """
    blocks = _blocks(section, r"^#{3,4}\s*(.+)$")
    if blocks:
        return blocks
    blocks = _blocks_by_key(section, "名字")
    if blocks:
        return blocks

    # 第 3 种：`- 某某：` 后面没有内容，且随后跟着「年龄/外观描述」等字段
    out: list[tuple[str, str]] = []
    name, buf = "", []
    for line in section.splitlines():
        m = re.match(r"\s*[-*]\s*([^：:]{1,16})[：:]\s*$", line)
        if m:
            if name:
                out.append((name, "\n".join(buf).strip()))
            name, buf = m.group(1).strip(), []
        elif name:
            buf.append(line)
    if name:
        out.append((name, "\n".join(buf).strip()))
    # 只有带外观/年龄的才算角色，避免把别的空值子弹点当成角色
    return [(n, b) for (n, b) in out
            if re.search(r"(年龄|外观描述|描述|服装锁|一致性锁)", b)]


CHECK_TAIL = re.compile(r"^\s*(☑|□|✅|\[.?\])\s*|^\s*【?输出前自检")


def _strip_checklist(body: str) -> str:
    """自检清单常被写在最后一镜后面，不能让它混进 prompt。"""
    keep = []
    for line in body.splitlines():
        if CHECK_TAIL.match(line):
            break
        keep.append(line)
    return "\n".join(keep).strip()



_DUR_RE = re.compile(r"[（(]\s*(\d+(?:\.\d+)?)\s*(?:s|S|秒)?\s*[）)]")


def _parse_duration(row_text: str, default: float = 5.0) -> float:
    """从「镜 1（8s）」里取时长；取不到用默认 5 秒。H3 只支持 4–15 秒。"""
    m = _DUR_RE.search(row_text or "")
    if not m:
        return default
    try:
        d = float(m.group(1))
    except ValueError:
        return default
    return max(4.0, min(15.0, d))

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
    # 角色是动物还是人：靠名字/描述里的物种词判断（只影响"关系"是否强制）
    ANIMAL_HINTS = ("犬", "狗", "猫", "马", "牛", "驴", "鸡", "鸭", "鹅", "羊", "猪", "兔",
                    "鸟", "鹰", "蛇", "猴", "狼", "虎", "鹿", "dog", "cat", "horse")

    def _species_of(cname: str, body: str) -> str:
        blob = f"{cname} {body}".lower()
        return "animal" if any(h.lower() in blob for h in ANIMAL_HINTS) else "human"

    char_blocks = _char_blocks(secs.get("角色卡", ""))
    for name, body in char_blocks:
        d = _bullets(body)
        raw_name = _pick(d, "名字") or name
        characters.append({
            "character_id": _ascii_id(clean_name(raw_name), "char"),
            "name": clean_name(raw_name),
            "species": _species_of(clean_name(raw_name), body),
            "description": _clean_md(_pick(d, "外观描述", "描述")),
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
        prompts[sid] = _strip_checklist(body)

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

    # 场景列可能写「地下宫殿」、「underground_palace」、「场景 1」、
    # 「庙会街（temple_fair）」—— 四种都要认，否则会退化成 scene_xxxx 哈希 ID。
    def _strip_paren(x: str) -> str:
        x = re.sub(r"[（(][^）)]*[）)]", "", x or "")
        return x.strip()

    scene_lookup: dict[str, str] = {}
    for i, s in enumerate(scenes, start=1):
        name, sid = s["name"], s["scene_id"]
        for key in (name, sid, sid.replace("_", " "), _strip_paren(name),
                    f"场景 {i}", f"场景{i}", f"场景 {i}：{name}", str(i)):
            if key:
                scene_lookup[key] = sid
        # 场景卡标题里的 "场景 N：名字" 也要能对上
        scene_lookup[f"场景 {i}：{name}（{sid}）"] = sid
    char_lookup = {c["name"]: c["character_id"] for c in characters}
    has_anchor_col = any("锚定" in h for h in header)

    shots = []
    for n, row in enumerate(body_rows, start=1):
        sid = _shot_number(cell(row, "镜号"), n)
        scene_label = cell(row, "场景")
        scene_label_clean = re.sub(r"[（(][^）)]*[）)]", "", scene_label or "").strip()
        scene_id = (scene_lookup.get(scene_label)
                    or scene_lookup.get(scene_label_clean)
                    or scene_lookup.get(scene_label.strip())
                    or (scene_label.strip() if scene_label.strip() in
                        {s["scene_id"] for s in scenes} else "")
                    or (scenes[0]["scene_id"] if len(scenes) == 1 else "")
                    or _ascii_id(scene_label, "scene"))
        cast_raw = cell(row, "出场角色", "角色")
        cast = []
        if cast_raw and "无" not in cast_raw:
            for part in re.split(r"[、,，/；;]\s*", cast_raw):
                part = clean_name(part.strip())
                if part:
                    cast.append(char_lookup.get(part, _ascii_id(part, "char")))
        dialogue = cell(row, "台词")
        # 「空 / 无 / -」都是"这一镜没台词"的常见写法，不能当成台词
        if dialogue and dialogue.strip() not in ("空", "无", "-", "—", "N/A", "n/a") \
                and "无" not in dialogue:
            if "<d>" not in dialogue:
                dialogue = f"<d>{dialogue}</d>"
        else:
            dialogue = ""
        trans = cell(row, "转场") or "cut"
        if has_anchor_col:
            anchor = "plate" if "plate" in (cell(row, "锚定") or "").lower() else ""
        else:
            anchor = "plate" if not cast else ""
        # 音效 / 配乐：H3 三字段里的 overall_soundscape 与 non_diegetic_music 的原料
        sound = cell(row, "音效", "声音", "soundscape").strip()
        if sound in ("无", "-", "N/A"):
            sound = ""
        music = cell(row, "配乐", "音乐", "music").strip()
        if music in ("无", "-", "N/A"):
            music = ""
        # 【接续】从 prompt 里读：无 / 接上一帧 / 姿势承接 / 动作中段
        _pr = prompts.get(sid, "") or ""
        _mm = re.search(r"【接续】\s*([^\n]*)", _pr)
        _raw = (_mm.group(1) if _mm else "").strip()
        if "接上一帧" in _raw:
            continuation = "frame_pin"
        elif "姿势承接" in _raw:
            continuation = "state_match"
        elif "动作中段" in _raw:
            continuation = "action_match"
        else:
            continuation = "cut"
        shots.append({
            "shot_id": sid,
            "scene_id": scene_id,
            "cast": cast,
            "continuation": continuation,
            "camera": cell(row, "景别", "机位"),
            "prompt": prompts.get(sid, ""),
            "dialogue": dialogue,
            "sound": sound,
            "music": music,
            "transition": "chain" if "chain" in trans.lower() else "cut",
            "anchor": anchor,
            "duration": _parse_duration(cell(row, "镜号")),
        })

    warnings = []
    # 场景列匹配不上时，不要静默变成哈希 ID，直接点名
    known_scene_ids = {s["scene_id"] for s in scenes}
    for s in shots:
        if s["scene_id"] not in known_scene_ids:
            warnings.append(
                f"{s['shot_id']} 的场景「{s['scene_id']}」没匹配到场景卡 —— "
                f"镜头表里的场景列要写场景卡的名字或 id（如「庙会街」或「temple_fair」）")
    # 章节缺失是最常见的"导入后一片红"的原因，直接点名
    for canon, label in (("项目", "项目"), ("角色卡", "角色卡"), ("场景卡", "场景卡"),
                         ("镜头表", "镜头表")):
        if not secs.get(canon, "").strip():
            warnings.append(f"没有找到「{label}」章节 —— 对应内容会是空的")
    if not characters:
        warnings.append("一个角色都没解析出来：角色卡下面每个角色要用 `### 名字` 开头")
    if not scenes:
        warnings.append("一个场景都没解析出来：场景卡下面每个场景要用 `### 场景 1：名字（id：xxx）` 开头")
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
