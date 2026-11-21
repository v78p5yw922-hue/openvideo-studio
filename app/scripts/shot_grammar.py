"""剧本 v3 的空间词表：解析 + 中英映射。

设计原则
--------
剧本里的空间关系必须是**封闭词表里的值**，不是自由散文。
这个模块只做三件事，不做任何语义猜测：
  1. 把 `景别=中景；高度=平视；…` 解析成 dict
  2. 校验值是否在允许集合里（越界就报出允许值清单）
  3. 查表映射成固定的英文短语（模型对英文空间词更稳定）

新增一种说法 = 往表里加一行；不要再写"如果出现某关键词就…"的分支。
"""

from __future__ import annotations

import re

# ------------------------------------------------------------------ 机位
CAMERA_ENUMS: dict[str, dict[str, str]] = {
    "景别": {
        "大特写": "extreme close-up",
        "特写": "close-up",
        "近景": "medium close-up",
        "中景": "medium shot",
        "中全": "medium-full shot",
        "全景": "full shot",
        "远景": "wide shot",
    },
    "高度": {
        "平视": "eye level",
        "略俯": "slightly high angle",
        "俯拍": "high angle",
        "略仰": "slightly low angle",
        "仰拍": "low angle",
    },
    # 带方位（-左 / -右）的值才能锁住人物在画面里的朝向；
    # 不带方位的旧值保留兼容，但会在校验里提示"建议补方位"。
    "角度": {
        "正面": "frontal",
        "过肩": "over-the-shoulder",
        "过肩-左": "over the left shoulder",
        "过肩-右": "over the right shoulder",
        "侧对": "profile",
        "侧对-左": "profile from the left",
        "侧对-右": "profile from the right",
        "背拍": "from behind",
        "背拍-左后": "from behind, over the left shoulder",
        "背拍-右后": "from behind, over the right shoulder",
        "反打": "reverse angle",
    },
    "运镜": {
        "固定": "static camera",
        "缓推": "slow push-in",
        "缓拉": "slow pull-out",
        "横移": "lateral track",
        "跟拍": "follow shot",
        "手持": "handheld",
        # 只允许小幅（约 30°）弧线：模型没有三维空间，大角度会重新编背景
        "环绕-左": "camera slowly arcs around the subject to the left, about 30 degrees",
        "环绕-右": "camera slowly arcs around the subject to the right, about 30 degrees",
    },
}

CAMERA_REQUIRED = ("景别", "高度", "角度", "运镜", "距离")

# 这些角度带"侧向"属性，不写左右就会越轴（人物在画面里的朝向会翻）
SIDE_SENSITIVE_ANGLES = ("过肩", "侧对", "背拍")

# ------------------------------------------------------------------ 人物
PERSON_ENUMS: dict[str, dict[str, str]] = {
    "位置": {
        "左": "left of frame",
        "中": "center of frame",
        "右": "right of frame",
        "左前": "front-left of frame",
        "右前": "front-right of frame",
    },
    "层级": {
        "前景": "foreground",
        "中景": "midground",
        "后景": "background",
    },
    "朝向": {
        "面向镜头": "facing the camera",
        "背对镜头": "back to camera",
        "左3-4侧": "three-quarter turn to the left",
        "右3-4侧": "three-quarter turn to the right",
        "纯左侧": "left profile",
        "纯右侧": "right profile",
        "仰视镜头": "looking up at the camera",
    },
    "视线": {
        "看对方": None,      # 需要对象，见 person_to_en
        "看镜头": "looking at the camera",
        "看画外": "looking off-screen",
        "闭眼": "eyes closed",
        "看手中物": "looking down at what they hold",
    },
}

PERSON_REQUIRED = ("位置", "层级", "朝向", "视线", "占比")

# ------------------------------------------------------------------ 关系
RELATIONS: dict[str, str] = {
    "面对面": "the two face each other, their eye lines meet",
    "并肩": "standing side by side, both facing the same direction",
    "一前一后": "one behind the other, both facing the same direction",
    "对面而坐": "seated facing each other across a surface",
    "擦肩": "passing each other in opposite directions",
    "背对背": "standing back to back",
    # 人和动物同框（狗、猫、鸡鸭、牛马）：写"谁在谁身边"
    "蹲在脚边": "sitting at the person's feet",
    "伏在膝前": "lying in front of the person's knees",
    "跟在身后": "following a step behind the person",
    "扑向人": "springing up against the person",
    "抱在怀里": "held in the person's arms",
    "隔在两人之间": "standing between the two people",
}

# 这些关系是"人和动物"用的；只涉及人的镜头别用
ANIMAL_RELATIONS = ("蹲在脚边", "伏在膝前", "跟在身后", "扑向人", "抱在怀里", "隔在两人之间")

KV_RE = re.compile(r"([^\s=；;，,：:]+)\s*=\s*([^\s；;，,]+)")
PERSON_LINE_RE = re.compile(r"^\s*[-•·]?\s*(?P<name>[^：:=]{1,20})[：:]\s*(?P<body>.+)$")


def _kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in KV_RE.findall(text or ""):
        out[k.strip()] = v.strip()
    return out


def looks_structured(text: str) -> bool:
    """值里带 `键=值` 才算 v3 结构；否则当 v2 自由文本处理。"""
    return "=" in (text or "")


def parse_camera(text: str) -> dict | None:
    """`景别=中景；高度=平视；角度=过肩；运镜=缓推；距离=1.5` → dict。"""
    if not looks_structured(text):
        return None
    return _kv(text)


def parse_people(text: str) -> list[dict]:
    """每行一个角色：`- 叶一恒：位置=左前；层级=前景；…`"""
    out: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = PERSON_LINE_RE.match(line)
        if not m:
            continue
        body = _kv(m.group("body"))
        if not body:
            continue
        item = {"name": m.group("name").strip()}
        item.update(body)
        out.append(item)
    return out


def parse_relation(text: str) -> str | None:
    v = (text or "").strip()
    if not v or v in ("无", "-", "—"):
        return None
    return v


# ------------------------------------------------------------------ 校验
def _issue(slot: str, field: str, who: str, code: str, message: str,
           allowed: list[str]) -> dict:
    return {"slot": slot, "field": field, "who": who, "code": code,
            "message": message, "allowed": allowed}


def _camera_allowed(field: str) -> list[str]:
    return ["0.3–10 米"] if field == "距离" else list(CAMERA_ENUMS[field])


def _person_allowed(field: str) -> list[str]:
    return ["20–80%"] if field == "占比" else list(PERSON_ENUMS[field])


def camera_issues(cam: dict) -> list[dict]:
    """机位的结构化问题；旧 check_camera 由此保留元组契约。"""
    issues: list[dict] = []
    for field in CAMERA_REQUIRED:
        val = cam.get(field)
        allowed = _camera_allowed(field)
        if not val:
            issues.append(_issue("机位", field, "", "CAMERA_ENUM_MISSING",
                                 f"机位缺「{field}」", allowed))
            continue
        if field == "距离":
            try:
                d = float(val.replace("米", "").replace("m", ""))
            except ValueError:
                issues.append(_issue("机位", field, "", "CAMERA_ENUM_INVALID",
                                     f"距离「{val}」不是数字（单位米）", allowed))
                continue
            if not 0.3 <= d <= 10:
                issues.append(_issue("机位", field, "", "CAMERA_ENUM_INVALID",
                                     f"距离 {d} 米超出 0.3–10", allowed))
            continue
        if val not in CAMERA_ENUMS[field]:
            issues.append(_issue("机位", field, "", "CAMERA_ENUM_INVALID",
                                 f"{field}「{val}」不在允许值：" + "/".join(CAMERA_ENUMS[field]),
                                 allowed))
    return issues


def people_issues(people: list[dict], cast_names: list[str]) -> list[dict]:
    """人物的结构化问题；cast_names 必须是角色名，不允许把角色 ID 暴露给 UI。"""
    issues: list[dict] = []
    for p in people:
        who = p.get("name") or "?"
        for field in PERSON_REQUIRED:
            if not p.get(field):
                issues.append(_issue("人物", field, who, "PERSON_ENUM_MISSING",
                                     f"人物缺「{field}」", _person_allowed(field)))
        for field in ("位置", "层级", "朝向"):
            val = p.get(field)
            if val and val not in PERSON_ENUMS[field]:
                issues.append(_issue(
                    "人物", field, who, "PERSON_ENUM_INVALID",
                    f"{field}「{val}」不在允许值：" + "/".join(PERSON_ENUMS[field]),
                    list(PERSON_ENUMS[field]),
                ))
        gaze = p.get("视线")
        if gaze and gaze != "看对方" and gaze not in PERSON_ENUMS["视线"]:
            issues.append(_issue(
                "人物", "视线", who, "PERSON_ENUM_INVALID",
                f"视线「{gaze}」不在允许值：" + "/".join(PERSON_ENUMS["视线"]),
                list(PERSON_ENUMS["视线"]),
            ))
        if gaze == "看对方":
            target = p.get("对象")
            if not target:
                issues.append(_issue("人物", "对象", who, "EYELINE_TARGET_MISSING",
                                     "视线=看对方 但没有写「对象」", list(cast_names)))
            elif target not in cast_names:
                issues.append(_issue("人物", "对象", who, "EYELINE_TARGET_NOT_IN_CAST",
                                     f"对象「{target}」不在本镜出场角色里", list(cast_names)))
        scale = p.get("占比")
        if scale:
            try:
                n = int(str(scale).replace("%", ""))
            except ValueError:
                issues.append(_issue("人物", "占比", who, "PERSON_SCALE_RANGE",
                                     f"占比「{scale}」不是整数", ["20–80%"]))
            else:
                if not 20 <= n <= 80:
                    issues.append(_issue("人物", "占比", who, "PERSON_SCALE_RANGE",
                                         f"占比 {n}% 超出 20–80", ["20–80%"]))
    return issues


def relation_issues(relation: str | None, n_people: int,
                    n_humans: int | None = None) -> list[dict]:
    """n_people = 本镜主体数；n_humans = 其中人的数量（默认等于 n_people）。

    【关系】描述的是人与人之间的站位；只有 1 个人 + 1 条狗时不强制（用【同框】或散文写更自然）。
    """
    humans = n_people if n_humans is None else n_humans
    if humans < 2:
        return []
    if not relation:
        return [_issue("关系", "关系", "", "RELATION_REQUIRED",
                       "两个及以上角色必须写【关系】", list(RELATIONS))]
    if relation not in RELATIONS:
        return [_issue("关系", "关系", "", "RELATION_INVALID",
                       f"关系「{relation}」不在允许值：" + "/".join(RELATIONS),
                       list(RELATIONS))]
    return []


def check_camera(cam: dict) -> list[tuple[str, str]]:
    """返回 [(code, msg)]；空列表表示合格。"""
    return [(i["code"], i["message"]) for i in camera_issues(cam)]


def check_people(people: list[dict], cast_names: list[str]) -> list[tuple[str, str, str]]:
    """返回 [(code, who, msg)]。"""
    return [(i["code"], i["who"], i["message"]) for i in people_issues(people, cast_names)]


def check_relation(relation: str | None, n_people: int,
                   n_humans: int | None = None) -> list[tuple[str, str]]:
    return [(i["code"], i["message"])
            for i in relation_issues(relation, n_people, n_humans)]


# ------------------------------------------------------------------ 映射成英文
def camera_to_en(cam: dict) -> str:
    bits = []
    for field in ("景别", "高度", "角度", "运镜"):
        val = cam.get(field)
        if val and val in CAMERA_ENUMS[field]:
            bits.append(CAMERA_ENUMS[field][val])
    dist = cam.get("距离")
    if dist:
        d = str(dist).replace("米", "").replace("m", "").strip()
        bits.append(f"{d} meters from the subject")
    return ", ".join(bits)


def person_to_en(p: dict) -> str:
    bits: list[str] = []
    pos = p.get("位置")
    layer = p.get("层级")
    if pos and pos in PERSON_ENUMS["位置"]:
        bits.append(PERSON_ENUMS["位置"][pos])
    if layer and layer in PERSON_ENUMS["层级"]:
        bits.append(PERSON_ENUMS["层级"][layer])
    facing = p.get("朝向")
    if facing and facing in PERSON_ENUMS["朝向"]:
        bits.append(PERSON_ENUMS["朝向"][facing])
    gaze = p.get("视线")
    if gaze == "看对方":
        target = p.get("对象")
        if target:
            bits.append(f"looking at {target}")
    elif gaze and gaze in PERSON_ENUMS["视线"] and PERSON_ENUMS["视线"][gaze]:
        bits.append(PERSON_ENUMS["视线"][gaze])
    scale = p.get("占比")
    if scale:
        n = str(scale).replace("%", "").strip()
        bits.append(f"occupies {n}% of frame height")
    return f"{p.get('name', '')} ({', '.join(bits)})" if bits else str(p.get("name", ""))


def relation_to_en(relation: str | None, names: list[str] | None = None) -> str:
    """关系 → 英文。

    注意：`the two face each other` 只对两人成立；三人（含动物）时必须改写，
    否则模型的指代数量对不上（实测会画出第四个人，或把狗当成人）。
    """
    if not relation:
        return ""
    names = [x for x in (names or []) if x]
    text = RELATIONS.get(relation, "")
    if relation == "面对面" and len(names) >= 3:
        return (f"{names[0]} and {names[1]} face each other; "
                f"{'、'.join(names[2:])} stay beside them")
    return text


def _structured_source(values: dict) -> str:
    return "；".join(f"{key}={value}" for key, value in values.items())


def compose_segments(cam: dict | None, people: list[dict],
                     relation: str | None) -> list[dict]:
    """细分空间描述；分隔符也独立成段，拼接结果与旧 compose 完全一致。"""
    groups: list[list[dict]] = []
    if cam:
        text = camera_to_en(cam)
        if text:
            groups.append([{"slot": "机位", "kind": "en",
                            "source": _structured_source(cam), "text": text}])
    if people:
        person_texts = [person_to_en(person) for person in people]
        if "; ".join(person_texts):
            group: list[dict] = []
            for index, (person, text) in enumerate(zip(people, person_texts)):
                if index:
                    group.append({"slot": "人物", "kind": "meta", "source": "", "text": "; "})
                source = f"{person.get('name', '')}：{_structured_source({k: v for k, v in person.items() if k != 'name'})}"
                group.append({"slot": "人物", "kind": "en", "source": source, "text": text})
            groups.append(group)
    rel = relation_to_en(relation, [str(p.get("name") or "") for p in (people or [])])
    if rel:
        groups.append([{"slot": "关系", "kind": "en", "source": relation or "", "text": rel}])

    segments: list[dict] = []
    for index, group in enumerate(groups):
        if index:
            segments.append({"slot": "空间", "kind": "meta", "source": "", "text": ". "})
        segments.extend(group)
    return segments


def compose(cam: dict | None, people: list[dict], relation: str | None) -> str:
    """把结构化字段拼成一段可执行的空间描述（全英文）。"""
    return "".join(segment["text"] for segment in compose_segments(cam, people, relation))

def check_angle_side(cam: dict, has_cast: bool) -> list[tuple[str, str]]:
    """有角色的镜头用了"侧向角度"但没写左右 → 必然越轴，给提示。"""
    if not has_cast:
        return []
    angle = (cam or {}).get("角度", "")
    if angle in SIDE_SENSITIVE_ANGLES:
        options = [v for v in CAMERA_ENUMS["角度"] if v.startswith(angle + "-")]
        return [("CAMERA_SIDE_UNSPECIFIED",
                 f"角度「{angle}」没写左右，人物朝向可能和上一镜相反（越轴）。"
                 f"建议改成：" + " / ".join(options))]
    return []

# ---------------------------------------------------------------- 语气（台词怎么说）
# H3 的语音是模型生成的，"怎么说"只能靠英文描述去带。
# 把中国编剧常用的语气词锁成固定英文，避免"愤怒地说"这种模型听不懂的写法。
TONE_ENUMS: dict[str, str] = {
    "压低": "in a lowered voice, almost under the breath",
    "急促": "quickly and urgently",
    "发颤": "with a slight tremor in the voice",
    "冷笑": "with a cold, humourless laugh",
    "咬牙": "through clenched teeth",
    "轻描淡写": "lightly, as if it hardly matters",
    "柔和": "softly and warmly, with a smile in the voice",
    "气声": "in a breathy half-whisper",
    "平静": "calmly and evenly",
    "压抑": "restrained, as if holding something back",
    "失控": "losing control of the volume",
}

# 这些是编剧最容易漏、模型最吃得进的身体部位
MICRO_PART_WORDS = (
    "眼", "眉", "鼻", "唇", "嘴", "喉", "下颌", "下巴", "耳", "颈", "指", "手",
    "肩", "呼吸", "瞳孔", "脸颊", "太阳穴", "眼皮", "睫毛", "腮", "额", "咬",
    "eye", "brow", "lip", "jaw", "throat", "finger", "shoulder", "breath",
)


def parse_tone(value: str | None) -> str | None:
    """解析语气值；不带语气返回 None。"""
    if not value:
        return None
    v = str(value).strip().strip("【】 ")
    return v or None


def tone_issues(value: str | None) -> list[dict]:
    v = parse_tone(value)
    if not v:
        return []
    if v not in TONE_ENUMS:
        return [{"slot": "语气", "field": "语气", "who": "", "code": "TONE_INVALID",
                 "message": f"语气「{v}」不在允许值", "allowed": list(TONE_ENUMS)}]
    return []


def tone_to_en(value: str | None) -> str:
    v = parse_tone(value)
    if not v or v not in TONE_ENUMS:
        return ""
    return TONE_ENUMS[v]
