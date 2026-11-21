#!/usr/bin/env python3
"""把剧本镜头编译成 MiniMax H3 的原生三字段提示词。

依据：`backends/h3/PROMPT_GRAMMAR.md`（官方 `VIDEO_PROMPT_WRITING_GUIDE_base_en` 的浓缩版）。

    integrated_multimodal_description: [Shot 1] <风格优先>。<机位>。<主体>。<动作>。<场景>。<台词>
    overall_soundscape: <画面世界内的声音>
    non_diegetic_music: <配器/速度/节奏，不写情绪词>

设计取舍：
- 语言跟输入走。官方指南是英文，但我们的剧本是中文；字段名保持英文（结构标记），
  正文保留中文，与实测可用的中文提示词一致。
- 剧本里的五槽（【机位】【人物】【动作】【场景】【画质】）优先解析成散文；
  没有槽位就退回 camera + 原文。
- 台词统一转成 `(S1) 说：<d>[中文] 原话</d>`，说话人按 cast 顺序编号。
- 配乐里的情绪词会被剥掉（指南明确要求不写 mood words），剥完为空则写 N/A。

独立模块：只用标准库，渲染器直接从 scripts/ 导入，不依赖 studio。
"""

from __future__ import annotations

import re

import shot_grammar as sg

# 【机位】…【人物】… 这类槽位（允许一行内连续出现）
SLOT_RE = re.compile(r"【([^】]{1,8})】\s*([^【\n]*)")

# 指南要求 non_diegetic_music 不写情绪词，只写器乐/速度/节奏
MOOD_WORDS = (
    "悲伤", "忧伤", "紧张", "压迫", "压抑", "沉重", "感动", "温暖", "热烈", "冷峻",
    "恐怖", "惊悚", "欢快", "激动", "悲壮", "凄凉", "舒缓", "治愈", "史诗感", "氛围感",
    "sad", "tense", "epic", "emotional", "warm", "dark", "moody",
)

SOUND_FALLBACK = "环境底噪持续，伴随轻微的动作声与布料摩擦声。"
MUSIC_OFF = "none (no score in this clip; music is added in post)"
MUSIC_NA = "N/A"



PLACEHOLDERS = {"无", "空镜", "无（空镜）", "无(空镜)", "无人物", "N/A", "n/a", "-", "—", "none", "None"}


def clean_val(s: str) -> str:
    """去掉占位符，把「甲 | 乙 | 丙」这种表格感的分隔转成中文逗号。"""
    v = (s or "").strip().strip("|").strip()
    if v in PLACEHOLDERS:
        return ""
    # 「无（空镜建立）」「无(无人)」这类纯占位描述也丢掉
    if re.fullmatch(r"无\s*[（(][^）)]*[）)]", v):
        return ""
    v = v.replace("；", "，")
    v = re.sub(r"\s*\|\s*", "，", v)
    v = re.sub(r"，{2,}", "，", v).strip("，")
    return v


# 项目级「视觉语言」里如果混进角色服装，模型会给每套衣服都画一个人
COSTUME_WORDS = (
    "robe", "jacket", "shirt", "coat", "gown", "armor", "uniform", "dress",
    "turtleneck", "trousers", "boots", "sash", "necklace",
    "官袍", "绸袍", "短打", "长衫", "锦袍", "铠甲", "战袍", "西装", "工装",
)


def style_costume_hits(style: str) -> list[str]:
    low = (style or "").lower()
    return [w for w in COSTUME_WORDS if w in low]


def subject_lock(names: list[str]) -> str:
    """人数约束：不写的话，模型会把场景里的路人、甚至别的角色都画出来。"""
    names = [n for n in (names or []) if n]
    if not names:
        return "画面中无人，只有环境与物件。"
    # 正向陈述（H3 没有 negative 分支，写"不要出现别人"等于把人塞进上下文）
    if len(names) == 1:
        return f"画面里的主体只有一位：{names[0]}。"
    return f"画面里的主体就是这 {len(names)} 位：{'、'.join(names)}。"


def clean_style(style: str) -> str:
    """项目级风格串里不该出现角色服装；出现就丢掉那一段，避免模型给每套衣服画一个人。"""
    if not style:
        return ""
    parts = [x.strip() for x in re.split(r"[,，;；、]", style) if x.strip()]
    keep = [x for x in parts if not style_costume_hits(x)]
    return "，".join(keep) if keep else ""


# 【节奏】0.0 站定 | 1.8 合扇 | 3.0 拱手 | 4.5 抬眼
BEAT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[：:，,]?\s*([^|｜;；]+)")
BEAT_SPLIT = re.compile(r"[|｜;；\n]")
# 只有明确写「切/转场/切到某景别」才算镜头切换；「转身」是动作，不是切镜
CUT_START_RE = re.compile(r"^(镜头)?(切到|切成|切至|切|转场)")
CUT_SIZE_RE = re.compile(r"切(近景|特写|中景|全景|远景|大特写)")


def is_cut_beat(desc: str) -> bool:
    d = (desc or "").strip()
    if "转身" in d or "转过来" in d:
        return bool(CUT_START_RE.match(d))
    return bool(CUT_START_RE.match(d)) or bool(CUT_SIZE_RE.search(d))


def parse_beats(text: str) -> list[tuple[float, str]]:
    """把【节奏】解析成 [(秒, 描述)]，秒数必须递增。"""
    out: list[tuple[float, str]] = []
    for chunk in BEAT_SPLIT.split(text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = BEAT_RE.match(chunk)
        if not m:
            continue
        try:
            sec = float(m.group(1))
        except ValueError:
            continue
        desc = m.group(2).strip(" ：:，,")
        if desc:
            out.append((sec, desc))
    out.sort(key=lambda x: x[0])
    return out


def fmt_ts(sec: float) -> str:
    return f"{int(sec) // 60:02d}:{sec % 60:06.3f}"


def beats_to_prose(beats: list[tuple[float, str]]) -> str:
    """转成 H3 认的时间轴句子；带「切/特写」的节拍写成镜头切换。"""
    if not beats:
        return ""
    parts = []
    for i, (sec, desc) in enumerate(beats):
        if i == 0 and sec <= 0.05:
            parts.append(f"开场：{desc}。")
            continue
        is_cut = is_cut_beat(desc)
        if is_cut:
            cleaned = re.sub(r"^(镜头)?(切到|切至|切|转到|转为)\s*", "", desc).strip("，, ")
            parts.append(f"At {fmt_ts(sec)}, the camera cuts to {cleaned or desc}。")
        else:
            parts.append(f"At {fmt_ts(sec)}, {desc}。")
    return "".join(parts)


def clean_desc(text: str) -> str:
    """去掉描述里的 markdown 强调符号（**粗体** 之类），模型会把星号当字符。"""
    return re.sub(r"\*\*|__|`", "", text or "").strip()


OUTFIT_RE = re.compile(r"(Wearing[^.]+\.|wearing[^.]+\.)")


def outfit_of(description: str) -> str:
    """从角色卡的英文描述里抽「Wearing ...」这半句，用来锁服装。"""
    m = OUTFIT_RE.search(description or "")
    return m.group(1).strip() if m else ""


def outfit_lock(pairs: list[tuple[str, str]]) -> str:
    bits = []
    for name, desc in pairs:
        o = outfit_of(desc)
        if o:
            bits.append(f"{name}：{o.rstrip('.')}")
    if not bits:
        return ""
    return "服装锁定 —— " + "；".join(bits) + "。（每个人物在整部片子里保持同一套服装）"

# 多行槽：v3 的【人物】要写多行，普通正则只取到行尾会把内容截掉
MULTILINE_SLOT_RE = re.compile(r"【([^】]{1,8})】\s*((?:[^【]|【(?!\w))*)", re.S)
MULTILINE_KEYS = {"人物"}


def parse_slots(text: str) -> dict[str, str]:
    """解析【槽位】。

    默认按行取（老格式一行一个槽）；
    【人物】特殊处理：一直取到下一个【】为止，因为 v3 每个角色占一行。
    """
    out: dict[str, str] = {}
    for m in MULTILINE_SLOT_RE.finditer(text or ""):
        key = m.group(1).strip()
        raw = m.group(2)
        if key in MULTILINE_KEYS:
            val = "\n".join(x.strip() for x in raw.strip().splitlines() if x.strip())
        else:
            val = raw.strip().splitlines()[0].strip(" 　|") if raw.strip() else ""
        # 结构化值（带 `键=值`）必须原样保留：clean_val 会把「；」换成「，」，
        # 导致 KV 解析把后面所有字段吞进第一个值里
        if "=" in val:
            val = val.strip()
        elif key not in ("节奏", "时间轴", "人物"):
            val = clean_val(val)
        else:
            val = val.strip()
        if val:
            out[key] = val
    return out


def strip_mood(music: str) -> str:
    """配乐只保留可执行信息：乐器、速度、节奏、力度。"""
    if not music:
        return ""
    t = music
    for w in MOOD_WORDS:
        t = t.replace(w, "")
    t = re.sub(r"[，,]\s*(?=[，,])", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" ，,、|")
    return t


def _sent(s: str) -> str:
    s = (s or "").strip().rstrip("。；;")
    return s + "。" if s else ""


def _speaker_ids(names: list[str]) -> dict[str, str]:
    return {n: f"(S{i + 1})" for i, n in enumerate(names[:3])}


def _dialogue_line(dialogue: str, names: list[str], lang: str = "中文",
                   tone: str = "", off_screen: bool = False) -> str:
    """`<d>叶一恒：台词</d>` -> `(S1) 叶一恒 开口说：<d>[中文] 台词</d>`"""
    raw = (dialogue or "").strip()
    if not raw:
        return ""
    m = re.search(r"<d>(.*?)</d>", raw, re.S)
    body = m.group(1).strip() if m else raw
    speaker, target = "", ""
    sm = re.match(r"^([^：:]{1,14})[：:]\s*(.+)$", body)
    if sm:
        speaker, body = sm.group(1).strip(), sm.group(2).strip()
    if "→" in speaker:                     # A→B：台词 = 对 B 说
        speaker, _, target = speaker.partition("→")
        speaker, target = speaker.strip(), target.strip()
    # 官方：(S) 编号索引【发声源】，不说话的角色不占号。
    # 我们的格式一镜只有一句台词 → 说话人永远是 (S1)。
    sid = "(S1)"
    who = f"{speaker} " if speaker else ""
    towards = f"对 {target} " if target else ""
    # ---- 标点规则（H3 官方）：句末必须有 . ? ! 之一；不用省略号 ----
    body = re.sub(r"…{2,}|\.{3,}", "…", body).strip()
    if body.endswith("…"):
        body = body[:-1].rstrip("，,、") + "。"
    body = body.replace("…", "，")           # 句中省略 → 逗号（省略号会拉低清晰度）
    body = re.sub(r"[—－]{2,}", "，", body)
    if not body.endswith(("。", "？", "！", "?", "!")):
        body += "。"

    if off_screen:
        verb = "says in an off-screen voiceover"
    else:
        verb = "开口说"
    if tone:
        verb = f"{verb}（{tone}）"
    lip = "（画外音：the on-screen character's lips remain completely closed）" if off_screen else ""
    return f"{who}{towards}{sid} {verb}：<d>[{lang}] {body}</d>{lip}"


def _part(slot: str, kind: str, source: str, text: str) -> dict:
    return {"slot": slot, "kind": kind, "source": source or "", "text": text}


def _append_part(parts: list[dict], slot: str, kind: str, source: str, text: str) -> None:
    if text:
        parts.append(_part(slot, kind, source, text))


def _slice_parts(parts: list[dict], start: int, end: int) -> list[dict]:
    """按拼接文本的字符区间切片，同时保留每段的着色元数据。"""
    out: list[dict] = []
    position = 0
    for part in parts:
        text = part["text"]
        next_position = position + len(text)
        left, right = max(start, position), min(end, next_position)
        if left < right:
            out.append(dict(part, text=text[left - position:right - position]))
        position = next_position
    return out


def _trim_parts(parts: list[dict]) -> list[dict]:
    raw = "".join(part["text"] for part in parts)
    stripped = raw.strip()
    if not stripped:
        return []
    start = len(raw) - len(raw.lstrip())
    return _slice_parts(parts, start, start + len(stripped))


def _sentence_parts(parts: list[dict], slot: str) -> list[dict]:
    """与 _sent 相同，但保留内容分段，句末标点独立为 meta。"""
    raw = "".join(part["text"] for part in parts)
    body = raw.strip().rstrip("。；;")
    if not body:
        return []
    start = len(raw) - len(raw.lstrip())
    out = _slice_parts(parts, start, start + len(body))
    out.append(_part(slot, "meta", "", "。"))
    return out


def _sentence(text: str, slot: str, kind: str = "zh", source: str = "") -> list[dict]:
    return _sentence_parts([_part(slot, kind, source, text)], slot)


def compile_shot_report(shot: dict, style: str = "", cast_names: list[str] | None = None,
                        scene: dict | None = None, lang: str = "中文",
                        instruction: str = "",
                        cast_descriptions: dict[str, str] | None = None) -> dict:
    """返回完整提示词、逐字符片段、结构校验及未采用槽位。"""
    prompt = shot.get("visual_prompt") or shot.get("prompt") or ""
    slots = parse_slots(prompt)

    camera = clean_val(slots.get("机位") or (shot.get("camera") or ""))
    people = clean_val(slots.get("人物"))
    # v3：机位/人物用 `键=值` 结构化写法时，走词表映射；否则沿用 v2 自由文本
    cam_struct = sg.parse_camera(camera) if camera else None
    people_struct = sg.parse_people(slots.get("人物") or "")
    relation = sg.parse_relation(slots.get("关系") or "")
    action = clean_val(slots.get("动作"))
    place = clean_val(slots.get("场景"))
    look = clean_val(slots.get("画质"))
    beat_slot = "节奏" if slots.get("节奏") else "时间轴"
    beats = parse_beats(slots.get("节奏") or slots.get("时间轴") or "")
    # 台词开口时间：默认 0.8s，可用【开口】覆盖
    try:
        open_at = float(slots.get("开口") or 0.8)
    except ValueError:
        open_at = 0.8

    names = cast_names or shot.get("characters_present") or []
    # 画外音：说话人背对镜头，或剧本显式写了【画外音】
    off_screen = False
    flag = str(slots.get("画外音") or "").strip().lower()
    if flag in ("1", "是", "true", "yes", "on"):
        off_screen = True
    else:
        speaker = ""
        m_dlg = re.search(r"<d>\s*([^：:<]{1,14})[：:]", shot.get("dialogue") or "")
        if m_dlg:
            speaker = m_dlg.group(1).split("→")[0].strip()
        for person in (people_struct or []):
            if str(person.get("name") or "") == speaker:
                if str(person.get("朝向") or "") in ("背对镜头", "背拍-左后", "背拍-右后"):
                    off_screen = True
                break
    dlg = _dialogue_line(shot.get("dialogue", ""), names, lang,
                         sg.tone_to_en(slots.get("语气")), off_screen)
    structured = bool(cam_struct or people_struct)

    errors: list[dict] = []
    if "=" in prompt:
        errors.extend(sg.camera_issues(cam_struct or {}))
        errors.extend(sg.people_issues(people_struct, names))
        errors.extend(sg.relation_issues(relation, max(len(people_struct), len(names)),
                                     None))

    dropped: list[dict] = []

    def drop(slot: str, source: str, reason: str, replaced_by: str = "") -> None:
        if source:
            dropped.append({"slot": slot, "source": source, "reason": reason,
                            "replaced_by": replaced_by})

    if action and beats:
        drop("动作", action, f"已用【{beat_slot}】替代", beat_slot)
    if slots.get("节奏") and slots.get("时间轴"):
        drop("时间轴", slots["时间轴"], "已用【节奏】替代", "节奏")
    if slots.get("开口") and not dlg:
        drop("开口", slots["开口"], "没有台词可调度")
    if slots.get("语气") and not dlg:
        drop("语气", slots["语气"], "没有台词可调度")
    if slots.get("关系") and not structured:
        drop("关系", slots["关系"], "自由文本路径不使用【关系】")
    known_slots = {"机位", "人物", "关系", "动作", "场景", "画质", "节奏", "时间轴", "语气",
                   "开口", "声音", "配乐"}
    for slot, source in slots.items():
        if slot not in known_slots:
            drop(slot, source, "未识别的槽位，未参与编译")
    if shot.get("soundscape") and slots.get("声音"):
        drop("声音", slots["声音"], "已被镜头表「音效」覆盖", "音效")
    if shot.get("music") and slots.get("配乐"):
        drop("配乐", slots["配乐"], "已被镜头表「配乐」覆盖", "配乐")

    desc_parts: list[dict] = [_part("结构", "meta", "", "[Shot 1] ")]
    style_source = style
    style = clean_style(style)
    if style:
        desc_parts.extend(_sentence(style, "风格", source=style_source))

    if structured:
        # 结构化：机位 + 每个人物 + 关系 → 独立英文片段
        spatial_parts = sg.compose_segments(cam_struct, people_struct, relation)
        desc_parts.extend(_sentence_parts(spatial_parts, "空间"))
    else:
        desc_parts.extend(_sentence(camera, "机位", source=camera))

    if not slots:                       # 没有槽位：camera + 原文
        body = prompt.strip()
        _append_part(desc_parts, "原文", "zh", prompt, body)
    else:
        # 结构化时人物已在 head 里，这里只留场景，避免同一段描述出现两次
        if structured:
            body_parts = _sentence(place, "场景", source=place)
        else:
            body_parts = (_sentence(people, "人物", source=people)
                          + _sentence(place, "场景", source=place))
        if body_parts:
            desc_parts.extend(body_parts)
        else:
            _append_part(desc_parts, "原文", "zh", prompt, prompt.strip())

    if not beats:
        desc_parts.extend(_sentence(action, "动作", source=action))

    # ---- 时间轴：台词 + 节拍合并排序 ----
    timeline: list[tuple[float, str]] = []
    if dlg:
        timeline.append((open_at, "speech"))
    for sec, desc in beats:
        timeline.append((sec, "beat:" + desc))
    timeline.sort(key=lambda x: x[0])

    for sec, item in timeline:
        if item == "speech":
            plain = dlg.rstrip()
            # </d> 是闭合标签，不是句末标点 —— 后面仍要补一个句号
            if plain.endswith("</d>"):
                tail = ""      # 正文自带句末标点（P0 规则），这里再补会变成 `</d>。`
            elif plain.endswith(("。", "！", "？")):
                tail = ""
            else:
                tail = "。"
            _append_part(desc_parts, "台词", "meta", "", f"At {fmt_ts(sec)}, ")
            _append_part(desc_parts, "台词", "zh", shot.get("dialogue", ""), dlg)
            _append_part(desc_parts, "台词", "meta", "", tail)
        elif item.startswith("beat:"):
            beat_desc = item[5:]
            if dlg and beat_desc:
                stripped = strip_speech_cues(beat_desc)
                if stripped:
                    beat_desc = stripped
            if sec <= 0.05:
                _append_part(desc_parts, beat_slot, "meta", "", "开场：")
                _append_part(desc_parts, beat_slot, "zh", beat_desc, beat_desc)
                _append_part(desc_parts, beat_slot, "meta", "", "。")
            elif is_cut_beat(beat_desc):
                cleaned = re.sub(r"^(镜头)?(切到|切至|切|转到|转为)\s*", "", beat_desc).strip("，, ")
                _append_part(desc_parts, beat_slot, "meta", "", f"At {fmt_ts(sec)}, ")
                _append_part(desc_parts, beat_slot, "en", beat_desc, "the camera cuts to ")
                _append_part(desc_parts, beat_slot, "zh", beat_desc, cleaned or beat_desc)
                _append_part(desc_parts, beat_slot, "meta", "", "。")
            else:
                _append_part(desc_parts, beat_slot, "meta", "", f"At {fmt_ts(sec)}, ")
                _append_part(desc_parts, beat_slot, "zh", beat_desc, beat_desc)
                _append_part(desc_parts, beat_slot, "meta", "", "。")

    lock = subject_lock(names)
    desc_parts.extend(_sentence(lock, "人数", source="、".join(names)))
    pairs = [(n, clean_desc((cast_descriptions or {}).get(n, ""))) for n in names]
    outfit = outfit_lock(pairs)
    desc_parts.extend(_sentence(outfit, "服装", source=outfit))
    if look:
        desc_parts.extend(_sentence(look, "画质", source=look))
    desc_parts = _trim_parts(desc_parts)

    sound_source = shot.get("soundscape") or slots.get("声音") or ""
    sound = clean_val(sound_source) or SOUND_FALLBACK
    music_source = shot.get("music") or slots.get("配乐") or ""
    music = strip_mood(clean_val(music_source)) or MUSIC_NA
    # 项目要求"关掉逐镜配乐"（后期整集铺一条）→ 明确告诉模型本镜不带配乐
    if str(shot.get("music_mode") or "").lower() in ("off", "none") or shot.get("no_music"):
        music_source, music = "", MUSIC_OFF

    segments: list[dict] = []
    if instruction:
        _append_part(segments, "首帧", "en", instruction, instruction.strip())
        _append_part(segments, "结构", "meta", "", "\n\n")
    _append_part(segments, "结构", "meta", "", "integrated_multimodal_description: ")
    segments.extend(desc_parts)
    if dlg:
        sound = f"{sound}；人声只有上面这一句台词"
    elif names:
        sound = f"{sound}；本镜只有环境声，无人声"
    _append_part(segments, "结构", "meta", "", "\n\noverall_soundscape: ")
    _append_part(segments, "声音", "zh", sound_source, sound)
    _append_part(segments, "结构", "meta", "", "\n\nnon_diegetic_music: ")
    _append_part(segments, "配乐", "zh" if music != MUSIC_NA else "meta", music_source, music)
    compiled = "".join(segment["text"] for segment in segments)
    return {"compiled": compiled, "segments": segments, "errors": errors, "dropped": dropped}


def compile_shot(shot: dict, style: str = "", cast_names: list[str] | None = None,
                 scene: dict | None = None, lang: str = "中文",
                 instruction: str = "", cast_descriptions: dict[str, str] | None = None) -> str:
    """返回三字段提示词。instruction 用于 i2v/chain 的首行说明。"""
    return compile_shot_report(shot, style, cast_names, scene, lang, instruction,
                               cast_descriptions)["compiled"]


# 发声动词：出现在【节奏】里就等于告诉模型"这里要出声"。
# 台词已经由【开口】单独编译成时间轴指令，节奏里再写一次 → 多出一个人声音节（"诶"）。
SPEECH_CUE_RE = re.compile(r"开口|说话|喊道|喊出声|叫了一声|嘟囔|说道|说着|念叨|出声")


def strip_speech_cues(desc: str) -> str:
    """剥掉一拍里的发声指令，只留可拍动作。

    "开口喊他、眼睛先笑起来、眼角挤出细纹" → "眼睛先笑起来、眼角挤出细纹"
    "开口嘟囔、下巴搁在膝盖上"             → "下巴搁在膝盖上"
    """
    if not desc or not SPEECH_CUE_RE.search(desc):
        return desc
    parts = re.split(r"[、，,]", desc)
    kept = [x.strip() for x in parts if x.strip() and not SPEECH_CUE_RE.search(x)]
    return "、".join(kept)


def has_effective_lock(shot: dict, lock_frame: object | None) -> bool:
    """首帧指令只对应真正会走 i2v 首帧输入的空镜。"""
    return lock_frame is not None and not bool(shot.get("characters_present") or [])


def i2v_instruction() -> str:
    return ("For the target video, at 0.00 seconds into the target video, "
            "<Picture 1> (from [Shot 1]) is fully referenced.")
