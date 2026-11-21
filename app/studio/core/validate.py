"""剧本契约校验器。

目标：任何符合 `docs/SCRIPT-FORMAT.md` 的剧本，导入后都能直接渲染；
不符合的，在 UI 里说清楚哪一镜、错在哪、怎么改。

规则分两级：
- error   阻断渲染（渲出来一定是废片，或根本渲不出来）
- warn    可以渲，但大概率效果差
"""

from __future__ import annotations

import re

from . import db, service

import sys
from pathlib import Path as _Path
_SCRIPTS = _Path(__file__).resolve().parents[2] / 'scripts'
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import shot_grammar as _grammar  # noqa: E402

# ── 词表从数据文件读（studio/data/script-glossary.json） ──────────────
# 换剧情只改 JSON，不改这里。文件缺失/坏掉时回落到下面的内置默认值。
import json as _json

_GLOSSARY_PATH = _Path(__file__).resolve().parents[1] / "data" / "script-glossary.json"
_GLOSSARY: dict = {}


def _load_glossary() -> dict:
    """基础词库 + 用户覆盖（studio-data/glossary-overlay.json）。"""
    try:
        from . import glossary_store
        return glossary_store.merged()
    except Exception:  # noqa: BLE001
        return {}


def _gloss(path: str, default):
    """按 "rules.xxx" 取值，取不到就用默认。"""
    cur = _GLOSSARY
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


_GLOSSARY = _load_glossary()

# H3 的帧数只能是 17k+5，24fps 下等价于这几个可用时长；
# 写 6.5 秒这种值，模型给不了，最后还是会就近吸附（等于白写）。
# H3 的合法帧数是 17k+5；5s=124 / 8s=192 / 10s=243 / 15s=362
CANONICAL_DURATIONS = (5.0, 8.0, 10.0, 15.0)

# 角色服装词：出现在项目级「视觉语言」里，会让模型给每套衣服都画一个人
COSTUME_WORDS = (
    "robe", "jacket", "shirt", "coat", "gown", "armor", "uniform", "dress",
    "turtleneck", "trousers", "boots", "sash", "necklace",
    "官袍", "绸袍", "短打", "长衫", "锦袍", "铠甲", "战袍", "西装", "工装", "长裙", "披风",
)

# 配乐里禁止的情绪词（官方指南要求只写器乐/速度/节奏）
MOOD_WORDS = (
    "悲伤", "忧伤", "紧张", "压迫", "压抑", "沉重", "感动", "温暖", "热烈", "冷峻",
    "恐怖", "惊悚", "欢快", "激动", "悲壮", "凄凉", "治愈",
    "sad", "tense", "epic", "emotional", "warm", "dark", "moody",
)

MAX_CAST = 3

# 节奏里出现这些词 = 又下了一次"开口"指令，会和【开口】打架
SPEECH_CUE_WORDS = tuple(_gloss("rules.speech_cue_words", (
    "开口", "喊道", "喊出声", "叫了一声", "嘟囔", "说道", "念叨", "出声")))
# ── 通用坑位词表（不依赖剧情） ───────────────────────────────────────────
# 需要"状态"的物件：只写名词，模型就给默认值（生肉、整块、湿的）
FOOD_NOUNS = tuple(_gloss("rules.food_nouns",
                          ("猪脸肉", "肉片", "卤肉", "熟食", "炒菜", "米饭")))
STATE_NOUNS = FOOD_NOUNS
SHAPE_WORDS = tuple(_gloss("rules.state_words",
                           ("整", "切", "片", "块", "丝", "丁", "薄", "厚")))
COOK_WORDS = tuple(_gloss("rules.cook_words",
                          ("生", "熟", "卤", "炒", "蒸", "煮", "煎", "炸", "烤", "炖", "焖", "凉拌")))
TEMP_WORDS = ("热", "凉", "冷", "烫", "冰")
STATE_WORDS = SHAPE_WORDS + COOK_WORDS + TEMP_WORDS + ("干", "湿", "新", "旧")
# 需要前置动作的后续动作 → 前置关键词
PRECONDITIONS = {
    "倒油": ("掀开锅盖", "开盖", "揭开锅盖"),
    "下锅": ("倒油", "热锅"),
    "火柴": ("把盆放下", "盆放到", "放下盆", "手空", "擦了两下"),
    "说话": ("睁眼", "睁开眼"),
}
# 台词里出现这些词 = 在复述画面
RESTATE_WORDS = ("给你买", "这是给", "拿着", "你看这", "我给你")

# 节奏里出现这些词 = 在描述声音事件；H3 可能当台词念出来（s01 实测）
SOUND_EVENT_WORDS = tuple(_gloss("rules.beat_forbidden_words", (
    "应了一声", "响了一声", "叫了一声", "喊了一声", "传来", "听见", "听到", "声音", "一声")))
# 否定型指令词（叙事里的"没有回应"不算，只收指令性词）
NEGATION_WORDS = ("不要", "禁止", "不可", "别把", "不要加", "不出现在")
CHARS_PER_SEC = 3.8       # 实测：edge-tts 中文约 3.8 字/秒
SPEECH_HEADROOM = 0.3     # 留给动作和呼吸
DIALOGUE_SEP = "→"


def _err(shot_id: str, code: str, msg: str, fix: str) -> dict:
    return {"shot_id": shot_id, "code": code, "level": "error", "msg": msg, "fix": fix}


def _warn(shot_id: str, code: str, msg: str, fix: str) -> dict:
    return {"shot_id": shot_id, "code": code, "level": "warn", "msg": msg, "fix": fix}


def split_dialogue(raw: str) -> tuple[str, str, str]:
    """把 `<d>说话人→对象：台词</d>` 拆成 (说话人, 对象, 台词)。没有对象时由调用方决定。"""
    body = (raw or "").replace("<d>", "").replace("</d>", "").strip()
    if not body:
        return "", "", ""
    head, sep, rest = body.partition("：") if "：" in body else body.partition(":")
    if not sep:
        return "", "", body
    speaker, target = head, ""
    if DIALOGUE_SEP in head:
        speaker, _, target = head.partition(DIALOGUE_SEP)
    return speaker.strip(), target.strip(), rest.strip()



def mbeat_raw(prompt: str) -> str:
    m = re.search(r"【节奏】([^【\n]*)", prompt or "")
    return (m.group(1).strip() if m else "")


REPEAT_FAIL_LIMIT = 3      # 同一镜 6 小时内失败这么多次就拦住
REPEAT_FAIL_WINDOW_H = 6


def recent_failures(project_id: str, shot_id: str) -> int:
    """统计某镜最近失败/取消的次数（用于阻止无限重渲）。"""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=REPEAT_FAIL_WINDOW_H)).isoformat()
    with db.txn() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE project_id=? AND payload LIKE ? "
            "AND state IN ('failed','cancelled') AND created_at > ?",
            (project_id, f'%"{shot_id}"%', cutoff)).fetchall()
    return len(rows)


def parse_beats_text(text: str) -> list[tuple[float, str]]:
    """从【节奏】解析出 (秒, 描述)。

    实际格式：`0.0 站定 | 1.6 拍下 | 3.4 抬眼`（数字后面直接跟空格，没有冒号）。
    所以按 | ; 换行 切分，每段只要求以「数字 + 可选秒/s + 可选冒号」开头。
    """
    out: list[tuple[float, str]] = []
    for part in re.split(r"[|;；\n]", str(text)):
        m = re.match(r"\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?\s*[:：,，]?\s*(.+?)\s*$", part)
        if m:
            try:
                out.append((float(m.group(1)), m.group(2)))
            except ValueError:
                continue
    return out


def _beats_of(prompt: str) -> str:
    """从提示词里取出【节奏】那一段。"""
    i = prompt.find("【节奏】")
    if i < 0:
        return ""
    j = prompt.find("【", i + 4)
    return prompt[i:j if j > 0 else len(prompt)]


def validate_project(project_id: str) -> dict:
    project = service.get_project(project_id) or {}
    shots = service.list_shots(project_id)
    scenes = {s["scene_id"]: s for s in service.list_scenes(project_id)}
    chars = {c["character_id"]: c for c in service.list_characters()}
    errors: list[dict] = []
    warnings: list[dict] = []

    # ---- 项目级：视觉语言不该写角色服装
    style = project.get("style") or ""
    hits = [w for w in COSTUME_WORDS if w in style.lower()]
    if hits:
        errors.append(_err("", "STYLE_COSTUME",
                           f"「视觉语言」里混进了角色服装：{'、'.join(hits[:4])}。"
                           "模型会给每套衣服各画一个人，出现多个人物并排。",
                           "视觉语言只写风格/光线/画质（如 cinematic, soft daylight, 35mm film）；"
                           "服装写进角色卡的外观描述。"))

    # ---- 逐镜
    prev_cast: list[str] | None = None
    for sh in shots:
        sid = sh["shot_id"]
        cast = [c for c in sh["cast"] if c]
        cast_names = [chars[c]["name"] for c in cast if c in chars]

        if len(cast) > MAX_CAST:
            errors.append(_err(sid, "CAST_TOO_MANY",
                               f"出场角色 {len(cast)} 个，超过上限 {MAX_CAST}。",
                               "拆成多个镜头，或删掉次要角色。"))
        for cid in cast:
            c = chars.get(cid)
            if not c:
                warnings.append(_warn(sid, "CAST_UNKNOWN",
                                      f"出场角色 {cid} 不在角色库里，画面里不会锁定长相。",
                                      "在角色卡里补上这个角色，或改成已有角色。"))
            elif not (c.get("refs") or []):
                warnings.append(_warn(sid, "CAST_NO_REF",
                                      f"{c['name']} 还没有参考图，人脸会随机漂移。",
                                      f"给 {c['name']} 上传 1–3 张参考图（同一套服装、同一光线）。"))

        # 场景必须存在
        sc = scenes.get(sh["scene_id"])
        if not sc:
            errors.append(_err(sid, "SCENE_UNKNOWN",
                               f"场景 {sh['scene_id']} 不存在。",
                               "在场景卡里补上这个场景，或改成已有场景。"))

        # 负向指令：H3 用 BasicGuider（cfg=1），没有 negative 分支，
        # 写"不要出现 X"等于把 X 塞进上下文（实测：多一碗饭、多一个瓶子）
        prompt_all = str(sh.get("prompt") or "")
        neg_hits = [w for w in NEGATION_WORDS if w in prompt_all]
        if neg_hits:
            warnings.append(_warn(
                sid, "NEGATION_SCAN",
                f"提示词里有「{'、'.join(neg_hits)}」这类否定指令：H3 没有 negative 分支，"
                "写了反而会把那个东西带进画面。",
                "改成正面陈述：只写画面里有什么（画面里的主体只有 X；桌上就这几样：…）。"))

        # 参考图槽位（人物 + 场景共享 9 槽）：被丢掉要让人知道
        plan = sh.get("ref_plan") or {}
        usage = plan.get("usage") or {}
        dropped = plan.get("dropped") or []
        if dropped:
            kinds: dict[str, int] = {}
            for d in dropped:
                kinds[d.get("kind", "?")] = kinds.get(d.get("kind", "?"), 0) + 1
            detail = "、".join(f"{k} {v} 张" for k, v in kinds.items())
            warnings.append(_warn(
                sid, "REF_SLOTS_DROPPED",
                f"参考图槽位不够：9 槽只放得下 {usage.get('total')} 张，"
                f"多出来的被丢掉（{detail}）。丢的若是场景图，模型会自己编环境。",
                "三人同框时每人 2 张就够；或把这一镜改成中景/近景收掉背景。"))
        if plan and cast:
            allocated = {s.get("owner") for s in (plan.get("slots") or [])
                         if s.get("kind") == "cast"}
            zero = [chars[c]["name"] for c in cast
                    if c in chars and chars[c]["name"] not in allocated
                    and (chars[c].get("refs") or [])]
            if zero:
                errors.append(_err(
                    sid, "REF_MEMBER_ZERO",
                    f"这些角色一个参考图槽都没分到：{'、'.join(zero)}。"
                    "渲出来会是陌生人脸，不该进渲染队列。",
                    "减掉其他角色的视角数（三人镜每人 2 张），或把这一镜拆开。"))
        if usage and usage.get("total", 0) >= usage.get("cap", 9) and not dropped:
            warnings.append(_warn(
                sid, "REF_SLOTS_FULL",
                f"9 个参考槽刚好用满（人物 {usage.get('cast')} · 场景 {usage.get('scene')} "
                f"· 末帧 {usage.get('frame')}）：这一镜的出图会明显变慢。",
                "平时够用；若变慢到 45 秒/步以上，把某个配角的参考图减到 1 张。"))

        # 锚定母版但没母版
        if sh.get("anchor") == "plate" and sc and not sc.get("plate_sha"):
            errors.append(_err(sid, "PLATE_MISSING",
                               f"锚定=plate，但场景「{sc.get('name') or sh['scene_id']}」没有母版图。",
                               "给该场景上传母版图，或把这一镜的锚定改成「无」。"))

        # 台词：说话人/对象必须在本镜出场角色里
        speaker, target, text = split_dialogue(sh.get("dialogue") or "")
        if text:
            if not speaker:
                errors.append(_err(sid, "DLG_NO_SPEAKER",
                                   "台词没写说话人。",
                                   "写成 <d>角色名：台词</d>（对谁说就写 <d>A→B：台词</d>）。"))
            elif speaker not in cast_names:
                errors.append(_err(sid, "DLG_SPEAKER_NOT_IN_CAST",
                                   f"台词是「{speaker}」说的，但他/她不在本镜出场角色里。",
                                   "把说话人加进「出场角色」，或改掉这句台词。"))
            if target and target not in cast_names:
                errors.append(_err(sid, "DLG_TARGET_NOT_IN_CAST",
                                   f"台词对象「{target}」不在本镜出场角色里，画面里没人可看。",
                                   f"把 {target} 加进出场角色（≤3 人），"
                                   "或改成独白 <d>说话人：台词</d>，或拆成单独的反应镜头。"))
            beats = ""
            m = re.search(r"【节奏】([^\n]*)", sh.get("prompt") or "")
            if m:
                beats = m.group(1)
            hits = [w for w in SPEECH_CUE_WORDS if w in beats]
            if hits:
                warnings.append(_warn(
                    sid, "SPEECH_CUE_DUPLICATE",
                    f"有台词，节奏里又写了「{'、'.join(hits)}」：会和台词抢发声，"
                    "开头容易多一个含糊的音节（听着像是「诶」）。",
                    "台词交给【开口】，节奏里只写动作（把「开口喊他」改成「抬眼看他」）。"))
            body = (text or "").strip()
            if re.search(r"…|\.{3,}", body):
                warnings.append(_warn(
                    sid, "DIALOGUE_ELLIPSIS",
                    "台词里用了省略号：H3 官方说省略号会拉低念白清晰度。",
                    "句尾的 … 换成 。，句中的 … 换成 ，；听不清的字写 [unclear]。"))
            if body and not body.endswith(("。", "？", "！", "?", "!")):
                warnings.append(_warn(
                    sid, "DIALOGUE_PUNCT",
                    "台词没有句末标点：官方要求句末有 . ? ! 之一再结束。",
                    f"在「{body[-12:]}」后面补 。 / ？ / ！"))
            if speaker and target and speaker == target:
                warnings.append(_warn(sid, "DLG_SELF",
                                      "说话人和对象是同一个人。", "改成自语言或删掉对象。"))

        # 光脚 vs 服装锁里的鞋：模型会先穿鞋再"让鞋消失"（s01 实测）
        beats_txt = _beats_of(sh.get("prompt") or "")
        if beats_txt and any(w in beats_txt for w in ("光脚", "赤脚")):
            locked = []
            for cid in cast:
                outfit = (chars.get(cid) or {}).get("outfit_lock") or ""
                if any(w in outfit for w in ("鞋", "凉鞋", "布鞋", "靴")):
                    locked.append(chars[cid]["name"])
            if locked:
                warnings.append(_warn(
                    sid, "COSTUME_FOOTWEAR_CONFLICT",
                    f"节奏写「光脚/赤脚」，但 {('、'.join(locked))} 的服装锁里有鞋。",
                    "把服装锁改成「出屋时才穿鞋」，或在节奏里写明鞋摆在哪里。"))

        # ── 通用坑位检查 ──────────────────────────────────────────
        beats_all = _beats_of(sh.get("prompt") or "")
        if beats_all:
            # 1) 名词缺状态（生/熟/切/片…）
            for noun in STATE_NOUNS:
                if noun in beats_all:
                    idx = beats_all.find(noun)
                    window = beats_all[max(0, idx - 30): idx + 30]
                    food = noun in FOOD_NOUNS
                    ok = (any(w in window for w in COOK_WORDS) if food
                          else any(w in window for w in STATE_WORDS))
                    if not ok:
                        warnings.append(_warn(
                            sid, "STATE_MISSING",
                            f"节奏里有「{noun}」但没写状态（生的？熟的？整的？切的？）。",
                            "补一个状态词：卤好的／切成薄片／热的／整棵的。"))
                        break

            # 2) 后续动作缺前置
            for action, pres in PRECONDITIONS.items():
                if action == "说话" and not any(w in beats_all for w in ("睡", "闭眼", "还没醒")):
                    continue          # 没在睡就不需要写"睁眼"
                if action in beats_all and not any(k in beats_all for k in pres):
                    warnings.append(_warn(
                        sid, "ACTION_PRECONDITION",
                        f"节奏里写了「{action}」，但没有前置动作（{'/'.join(pres[:2])}）。",
                        "把前置动作写进节奏：先掀开锅盖，再倒油。"))
                    break

            # 3) 手里还拿着东西
            reach = any(w in beats_all for w in ("去够", "拿起", "伸手去", "踮脚"))
            hold = None
            if reach:
                for item in ("搪瓷盆", "篮子", "竹篮", "碗"):
                    if item not in beats_all:
                        continue
                    # 只要有一处交代了"放下/放到"，就算合格
                    handled = any(w in beats_all for w in ("放下", "放到", "搁在"))
                    if not handled:
                        hold = item
                        break
            if hold:
                warnings.append(_warn(
                    sid, "PROP_IN_HAND",
                    f"节奏里出现过「{hold}」，但没写它什么时候放下/放到哪。",
                    "补一句：把盆放到灶台边／放下碗。"))

        # 4) 台词复述画面
        dlg_txt = sh.get("dialogue") or ""
        if dlg_txt and any(w in dlg_txt for w in RESTATE_WORDS):
            warnings.append(_warn(
                sid, "DIALOGUE_RESTATES",
                "台词在复述画面已经做过的事（给你买的／你看这）。",
                "台词只说画面说不出的：意图、情绪、关系。"))

        # 说话人必须在画内且脸可见：否则模型会把台词分给画内唯一有脸的人
        dlg_spk = str(sh.get("dialogue") or "").strip()
        if dlg_spk:
            m_sp = re.search(r"<d>\s*([^：:]{1,14})[：:]", dlg_spk)
            sp = m_sp.group(1).split("→")[0].strip() if m_sp else ""
            if sp and sp != "画外音":
                line = next((ln for ln in (sh.get("prompt") or "").splitlines()
                             if re.match(rf"^\s*-\s*{re.escape(sp)}\s*[:：]", ln)), "")
                if not line:
                    warnings.append(_warn(sid, "SPEAKER_NOT_VISIBLE",
                                          f"台词是「{sp}」说的，但他/她不在【人物】里。",
                                          "把说话人写进【人物】，并保证头与上半身在画面内。"))
                else:
                    share = re.search(r"占比=(\d+)", line)
                    layer = re.search(r"层级=([^；;\s]+)", line)
                    if layer and "前景" in layer.group(1):
                        warnings.append(_warn(sid, "SPEAKER_NOT_VISIBLE",
                                              f"说话人「{sp}」在前景，容易被裁掉。",
                                              "说话人放中景，或改成画外音。"))
                    if share and int(share.group(1)) < 28:
                        warnings.append(_warn(sid, "SPEAKER_NOT_VISIBLE",
                                              f"说话人「{sp}」占比只有 {share.group(1)}%，脸可能不在画内。",
                                              "提到 30% 以上，或在【场景】里写明她/他在画面内。"))

        # 接续：标了 frame_pin 就必须有上一镜可用的末帧，且首拍不能是新动作
        if str(sh.get("continuation") or "") == "frame_pin":
            idx = [s["shot_id"] for s in shots].index(sid)
            prev = shots[idx - 1] if idx > 0 else None
            if prev is None:
                errors.append(_err(sid, "CHAIN_NO_PREV_FRAME",
                                   "标了「接上一帧」，但这是第一个镜头，没有上一镜。",
                                   "改成「无」；接续只对第 2 镜起有意义。"))
            else:
                pt = service.latest_take(project_id, prev["shot_id"])
                if not pt:
                    warnings.append(_warn(sid, "CHAIN_NO_PREV_FRAME",
                                          f"标了「接上一帧」，但上一镜 {prev['shot_id']} 还没有片段。",
                                          "先渲上一镜；否则钉帧会退化成普通镜。"))
            first_beat = ""
            m = re.search(r"【节奏】\s*\d+(\.\d+)?\s*([^|]*)", sh.get("prompt") or "")
            if m:
                first_beat = m.group(2)
            NEW_ACTIONS = ("坐起", "站起来", "转身", "伸手拿", "抬脚", "推开", "走进", "走出")
            if any(w in first_beat for w in NEW_ACTIONS):
                warnings.append(_warn(sid, "CHAIN_NEW_ACTION",
                                      f"标了「接上一帧」，但首拍是「{first_beat.strip()[:16]}」这类新动作。",
                                      "钉帧会让模型保持上一镜姿势；新动作镜请改成「无」。"))

        # 节奏里不许写声音事件（模型会念出来）
        beats = _beats_of(sh.get("prompt") or "")
        if beats:
            hit = [w for w in SOUND_EVENT_WORDS if w in beats]
            if hit:
                warnings.append(_warn(
                    sid, "BEAT_SOUND",
                    f"节奏里写了声音事件（{chr(12289).join(hit)}）：H3 会把它当台词念出来。",
                    "把声音写进【音效】，节奏只写看得见的动作（手/脚/眼睛/嘴/身体）。"))

        # 时长与台词容量
        dur = float(sh.get("duration") or 5.0)
        if dur < 4 or dur > 15:
            errors.append(_err(sid, "DURATION_RANGE",
                               f"镜头时长 {dur:g}s 超出 H3 的 4–15 秒范围。",
                               "改成 4–15 之间的时长（写在镜号里，如「镜 2（8s）」）。"))
        if text:
            need = len(re.sub(r"[，。！？、—…\s]", "", text)) / CHARS_PER_SEC + SPEECH_HEADROOM
            if need > dur:
                errors.append(_err(
                    sid, "DIALOGUE_TOO_LONG",
                    f"台词约需 {need:.1f} 秒，超过这一镜的 {dur:g} 秒。",
                    f"删到 {int((dur - SPEECH_HEADROOM) * CHARS_PER_SEC)} 字以内，"
                    f"或把镜号改成「镜 N（{min(15, max(4, int(need) + 1))}s）」拆开念。"))
            if re.search(r"[。！？]", text[1:-1] or ""):
                warnings.append(_warn(sid, "DIALOGUE_MULTI_SENTENCE",
                                      "这一镜的台词有多句，一镜只适合一个语气转折。",
                                      "拆成两镜，或合并成一句。"))

        # 反复失败：拦住无限重渲
        fails = recent_failures(project_id, sid)
        if fails >= REPEAT_FAIL_LIMIT:
            warnings.append(_warn(
                sid, "REPEAT_FAILURE",
                f"这一镜最近已经失败/取消 {fails} 次，继续重渲很可能还是同样的问题。",
                "先看「上次失败」的错误信息或引擎日志；确认要再试就勾选「强制重渲」。"))

        # 饱满度：5 秒里既没人物、又没节拍，就是空镜
        if not cast_names and not mbeat_raw(sh.get("prompt") or ""):
            warnings.append(_warn(
                sid, "EMPTY_SHOT",
                "这一镜没有人物、也没有【节奏】，5 秒会显得很空。",
                "至少加一个出场角色；实在要空镜，就把它压进下一镜的 0.0–1.5 秒当开场节拍。"))

        # 有人物 + 首帧：合法需求，但当前渲染路径只能用其一（i2v 分支不带角色参考）
        if cast_names and sh.get("first_frame"):
            warnings.append(_warn(sid, "FIRST_FRAME_WITH_CAST",
                                  "这一镜有人物又有首帧图：人物镜走的是角色参考路径，"
                                  "首帧不会当作「第一帧」使用。",
                                  "想要接上一镜，勾选检查器里的「接上一镜末帧（人物镜）」——"
                                  "它会把末帧作为第二张场景参考图传给模型（近似接续，不是像素级锁定）。"))

        # 时间轴节拍：不能超过镜头时长
        prompt_src = sh.get("prompt") or ""
        beat_raw = ""
        mbeat = re.search(r"【节奏】([^【\n]*)", prompt_src)
        if mbeat:
            beat_raw = mbeat.group(1)
        if beat_raw:
            times = []
            for chunk in re.split(r"[|｜;；\n]", beat_raw):
                m = re.match(r"\s*(\d+(?:\.\d+)?)", chunk or "")
                if m:
                    times.append(float(m.group(1)))
            if times and max(times) >= dur:
                errors.append(_err(
                    sid, "BEAT_OVERRUN",
                    f"节奏里最后一个节拍在 {max(times):g} 秒，但这一镜只有 {dur:g} 秒。",
                    "把节拍时间往前挪，或把镜号改成更长的时间（如「镜 N（8s）」）。"))
            if len(times) >= 2 and times != sorted(times):
                warnings.append(_warn(sid, "BEAT_UNSORTED",
                                      "节奏里的时间不是递增的。", "按 0.0 / 1.8 / 3.0 的顺序写。"))

        # 提示词结构
        prompt = sh.get("prompt") or ""
        slots = {k: v for k, v in re.findall(r"【([^】]{1,8})】\s*([^【\n]*)", prompt)}
        cam = (sh.get("camera") or "").strip()
        # v3：机位写在【机位】槽里且是 `键=值` 形式 → 交给词表规则，旧的关键词检查跳过
        structured_cam = "=" in (slots.get("机位") or "")
        if not cam and not structured_cam:
            warnings.append(_warn(sid, "NO_CAMERA", "没写机位。", "补「景别/机位」列。"))
        elif not structured_cam:
            missing = []
            if not any(k in cam for k in ("平视", "俯拍", "俯视", "仰拍", "仰视", "贴地")):
                missing.append("平视/俯仰")
            if not any(k in cam for k in ("距", "米", "m", "贴近", "远")):
                missing.append("距离")
            if missing:
                warnings.append(_warn(
                    sid, "CAMERA_INCOMPLETE",
                    f"机位没写全，缺：{'、'.join(missing)}。模型会自己发挥（实测长出了仰角远景）。",
                    "补成「中景双人平视固定，与两人眼高齐平，距人物 1.2 米」这种程度。"))
        if not slots:
            warnings.append(_warn(sid, "PROMPT_NO_SLOTS",
                                  "这一镜的 prompt 不是【机位】【人物】【动作】【场景】【画质】结构，"
                                  "模型只能自由发挥。",
                                  "按五槽格式重写这一镜的 prompt。"))
        elif not (slots.get("动作") or "").strip() \
                and "节奏" not in slots and "时间轴" not in slots:
            warnings.append(_warn(sid, "NO_ACTION", "没写动作，也没写【节奏】。",
                                  "补【动作】或【节奏】（三拍：0.0 起 / 1.7 变 / 3.4 收）。"))

        # 空镜但 prompt 里提到了角色
        if not cast_names and prompt:
            for c in chars.values():
                if c["name"] and c["name"] in prompt:
                    warnings.append(_warn(sid, "EMPTY_SHOT_MENTIONS_CHAR",
                                          f"这一镜没有出场角色，但 prompt 里提到了「{c['name']}」。",
                                          "把角色加进出场角色，或从 prompt 里删掉。"))
                    break

        # chain 跨换人
        if sh.get("transition") == "chain" and prev_cast is not None \
                and set(cast) != set(prev_cast):
            warnings.append(_warn(sid, "CHAIN_CAST_CHANGE",
                                  "这一镜接上一镜末帧，但两镜出场角色不同，画面会几乎不动。",
                                  "改成 cut；若仍想保留画面连续性，"
                                  "勾选这一镜的「接上一镜末帧（人物镜）」。"))
        prev_cast = cast

        # 配乐情绪词
        music = sh.get("music") or ""
        bad = [w for w in MOOD_WORDS if w in music.lower()]
        if bad:
            warnings.append(_warn(sid, "MUSIC_MOOD",
                                  f"配乐写了情绪词（{'、'.join(bad[:3])}），"
                                  "官方要求只写器乐/速度/节奏。",
                                  "改成「低音提琴长音，速度缓慢」这种可执行描述。"))

        # ---------------- 饱满度：几秒的镜头为什么"空" ----------------
        # ① 节拍太少：5 秒只写一个状态 = 站着不动
        beat_txt = f"{sh.get('action') or ''} {sh.get('prompt') or ''}"
        beats_n = len(parse_beats_text(beat_txt))
        if dur >= 4.5 and beats_n < 2:
            warnings.append(_warn(
                sid, "BEATS_FEW",
                f"{dur:g} 秒只有 {beats_n} 个节拍，画面会像定格。",
                "【节奏】至少写 2 拍：0.0 起手 + 中途一个变化（1.6–2.5 秒之间）。"))

        # ①.5 时长不是 H3 的原生档位
        if abs(dur - min(CANONICAL_DURATIONS, key=lambda x: abs(x - dur))) > 0.01:
            warnings.append(_warn(
                sid, "DURATION_NOT_CANONICAL",
                f"{dur:g} 秒不是 H3 的原生档位（只有 5 / 8 / 10 秒能精确对齐帧数）。",
                "改成 5、8 或 10 秒；否则模型会就近吸附，实际时长和写的不一致。"))

        # ② 台词说完后大段留白
        dlg = str(sh.get("dialogue") or "").strip()
        if dlg:
            plain = re.sub(r"<[^>]+>", "", dlg)
            plain = plain.split("：", 1)[-1].strip()
            if plain:
                open_at = float(sh.get("open_at") or 0.8)
                speech_end = open_at + len(plain) / CHARS_PER_SEC
                tail = dur - speech_end
                # 台词说完之后还安排了几个节拍？有戏的沉默不算空
                beats_after = [b for b, _ in parse_beats_text(mbeat_raw(sh.get("prompt") or ""))
                               if speech_end + 0.3 <= b < dur]
                # 台词太短：5 秒镜只有几个字，前后都会显得空
                speech_sec = len(plain) / CHARS_PER_SEC
                if speech_sec < 1.5 and dur >= 5:
                    warnings.append(_warn(
                        sid, "DIALOGUE_TOO_SHORT",
                        f"台词只有 {len(plain)} 字（约 {speech_sec:.1f} 秒），"
                        f"放在 {dur:g} 秒镜头里要空出 {dur - speech_sec:.1f} 秒。",
                        "三选一：① 把同一人的下一句并到这一镜 ② 开口调晚、前半用动作建立 "
                        "③ 改成画外音（上一镜给背影，声音从画外进来）"))

                if tail > 1.8 and len(beats_after) < 2:
                    warnings.append(_warn(
                        sid, "DEAD_AIR",
                        f"台词大约 {speech_end:.1f} 秒说完，之后 {tail:.1f} 秒里"
                        f"只有 {len(beats_after)} 个节拍，画面会停住。",
                        "在台词后补 2 个反应节拍（4.2 垂眼、5.6 呼气），"
                        "或者把时长改成 5 秒。"))

        # ②.2 对话别塞进小景别多人镜：正反打更稳
        _cam = getattr(_grammar, "parse_camera", None)
        _cam_struct = None
        try:
            from scripts.prompt_compiler import parse_slots as _ps2
            _cam_struct = _grammar.parse_camera((_ps2(prompt) or {}).get("机位") or "")
        except Exception:  # noqa: BLE001
            _cam_struct = None
        _size = ((_cam_struct or {}).get("景别") or "").strip()
        if dlg and len(cast_names) >= 2 and _size in ("大特写", "特写", "近景"):
            warnings.append(_warn(
                sid, "DIALOGUE_TWO_SHOT",
                f"{len(cast_names)} 人同框 + {_size} + 有台词：口型/身份/视线三样都容易崩。",
                "拆成正反打：A 单人近景（A 说）+ B 单人反打近景（只有反应）。"
                "两人各自用单人参考图，别让模型一次生成双人对白长镜头。"))

        # ②.5 语气 / 微表情：这两样缺了，画面就是"两个人对脸念台词"
        _slots = {}
        try:
            from scripts.prompt_compiler import parse_slots as _ps
            _slots = _ps(prompt) or {}
        except Exception:  # noqa: BLE001
            _slots = {}
        tone_val = _grammar.parse_tone((_slots.get("语气") or "").strip())
        if dlg and "=" in prompt and not tone_val:
            warnings.append(_warn(
                sid, "TONE_MISSING",
                "有台词但没写【语气】，模型会用最平淡的口气念。",
                "加一行如：【语气】压低  （可选：压低/急促/发颤/冷笑/咬牙/轻描淡写/气声/平静/压抑/失控）"))
        if dlg and tone_val and tone_val not in _grammar.TONE_ENUMS:
            warnings.append(_warn(
                sid, "TONE_INVALID",
                f"语气「{tone_val}」不在允许值。",
                "允许值：" + " / ".join(_grammar.TONE_ENUMS)))

        rhythm_txt = str(prompt or "")
        if dlg or cast_names:
            has_micro = any(w in rhythm_txt for w in _grammar.MICRO_PART_WORDS)
            if not has_micro:
                warnings.append(_warn(
                    sid, "MICRO_THIN",
                    "节奏里没有任何身体部位，画面里的人会像木头。",
                    "至少写 2 个：眼皮抬起 / 喉结滚动 / 鼻翼张开 / 指节泛白 / 胸口起伏"))

        # ③ 音效缺失
        sfx_txt = str(sh.get("sound") or "").strip()
        if not sfx_txt or sfx_txt in ("无", "-", "—"):
            warnings.append(_warn(
                sid, "SOUND_THIN",
                "没有音效，成片会显得干。",
                "写两层：环境底噪（风/香炉/烛芯）+ 动作声（布料/脚步/器皿）。"))

        # ④ 固定机位
        camera_txt = str(sh.get("camera") or "")
        if camera_txt and not re.search(r"推|拉|摇|移|跟|升|降|甩|环绕|轨道|手持|微移", camera_txt):
            warnings.append(_warn(
                sid, "CAMERA_STATIC",
                "固定机位。连续几镜都固定会像幻灯片。",
                "改成「近景平视缓推」或「中景平视微移」，给画面一点呼吸。"))

    # ---------------- 连续固定机位（项目级）----------------
    _shots_ordered = sorted(service.list_shots(project_id), key=lambda s: s["shot_id"])
    _run: list[str] = []
    for sh in _shots_ordered:
        cam = str(sh.get("camera") or "")
        if cam and not re.search(r"推|拉|摇|移|跟|升|降|甩|环绕|轨道|手持|微移", cam):
            _run.append(sh["shot_id"])
        else:
            if len(_run) >= 3:
                warnings.append(_warn(
                    _run[0], "CAMERA_STATIC_RUN",
                    f"连续 {len(_run)} 镜都是固定机位（{', '.join(_run)}），成片会像幻灯片。",
                    "中间至少一镜改成 缓推 / 横移 / 跟拍。"))
            _run = []
    if len(_run) >= 3:
        warnings.append(_warn(
            _run[0], "CAMERA_STATIC_RUN",
            f"连续 {len(_run)} 镜都是固定机位（{', '.join(_run)}），成片会像幻灯片。",
            "中间至少一镜改成 缓推 / 横移 / 跟拍。"))

    # ---------------- 角色一致性 ----------------
    HAIR_WORDS = ("髻", "刘海", "马尾", "束发", "披发", "散发", "topknot", "bangs",
                  "ponytail", "fringe", "bun", "braid")
    used_ids = {cid for sh in service.list_shots(project_id) for cid in (sh.get("cast") or [])}
    for c in service.list_characters(project_id):
        if c.get("character_id") not in used_ids:
            continue
        refs = c.get("refs") or []
        name = c.get("name") or c.get("character_id")
        cid = c.get("character_id")
        if len(refs) < 2:
            warnings.append({"shot_id": "", "character_id": cid, "code": "REF_TOO_FEW",
                             "level": "warn",
                             "msg": f"角色「{name}」只有 {len(refs)} 张参考图。单图渲染时长相和发型会漂。",
                             "fix": "至少 3 张：正面全身 / 3-4 侧面 / 侧面近景，同服装同发型同光线。"})
        desc = str(c.get("description") or "")
        hits = [w for w in HAIR_WORDS if w.lower() in desc.lower()]
        if hits and not c.get("refs_verified"):
            warnings.append({"shot_id": "", "character_id": cid, "code": "DESC_CONFLICT",
                             "level": "warn",
                             "msg": f"角色「{name}」描述里锁了发型（{'、'.join(hits[:3])}），"
                                    "请确认参考图和这段文字一致，否则每镜的发型会来回变。",
                             "fix": "对着描述重画参考图：束发就要图里也是束发、额前无刘海。"})

    # ---------------- v3 词表：机位/人物/关系 ----------------
    from . import script_import as _imp  # 延迟导入，避免循环
    for sh in service.list_shots(project_id):
        sid = sh["shot_id"]
        prompt = sh.get("prompt") or ""
        if "=" not in prompt:
            continue                      # v2 自由文本，不参与词表校验
        slots = _imp.parse_prompt_slots(prompt) if hasattr(_imp, "parse_prompt_slots") else {}
        if not slots:
            # 退路：自己按【】切
            import re as _re
            slots = {m.group(1): m.group(2) for m in
                     _re.finditer(r"【([^】]{1,8})】([^【]*)", prompt, _re.S)}
        cam = _grammar.parse_camera(slots.get("机位", ""))
        people = _grammar.parse_people(slots.get("人物", ""))
        relation = _grammar.parse_relation(slots.get("关系", ""))
        # cast 里存的是 character_id，视线对象写的是角色名 → 先映射
        _by_id = {c["character_id"]: c["name"] for c in service.list_characters(project_id)}
        cast_names = [_by_id.get(cid, cid) for cid in (sh.get("cast") or [])]

        # 侧向角度没写左右 → 人物朝向会和上一镜相反（越轴）
        for code, msg in _grammar.check_angle_side(cam or {}, bool(cast_names)):
            warnings.append(_warn(sid, code, msg,
                                  "在【机位】里写 角度=背拍-左后 这种带方位的值。"))

        for code, msg in _grammar.check_camera(cam or {}):
            errors.append(_err(sid, code, msg,
                               "按 SCRIPT-FORMAT-V3.md 的词表补全机位五要素。"))
        for code, who, msg in _grammar.check_people(people, cast_names):
            errors.append(_err(sid, code, f"{who}: {msg}",
                               "按 SCRIPT-FORMAT-V3.md 的【人物】词表改写，或使用允许值。"))
        # 关系只在"两个人及以上"时强制；1 人 + 动物不强制
        _info = {c["character_id"]: c for c in service.list_characters(project_id)}
        n_humans = sum(1 for cid in (sh.get("cast") or [])
                       if (_info.get(cid, {}).get("species") or "human") == "human")
        n_total = max(len(people), len(cast_names))
        for code, msg in _grammar.check_relation(relation, n_total, n_humans):
            errors.append(_err(sid, code, msg,
                               "两个及以上角色必须写【关系】，值见词表。"))
        # 人和动物同框：建议写"谁在谁身边"，否则模型容易把狗放到人身上
        has_animal = n_total > n_humans
        if has_animal and n_humans >= 1 and not relation:
            warnings.append(_warn(
                sid, "ANIMAL_RELATION_SUGGESTED",
                "这一镜有人和动物同框，但没写【关系】，模型可能把它们叠在一起。",
                "写一个空间关系：" + " / ".join(_grammar.ANIMAL_RELATIONS)))

    return {"project_id": project_id, "errors": errors, "warnings": warnings,
            "ok": not errors}
