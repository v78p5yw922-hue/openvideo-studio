#!/usr/bin/env python3
"""Validate a drama_script.json against the generic drama contract.

The contract is intentionally genre-free: characters, scenes and shots are the
only primitives. This validator is what the UI's 「校验脚本」 button calls, and
what render_drama.py refuses to run without.

Checks
------
1. project-level fields: project_id / title / target_duration / visual_style
2. target_duration vs sum(shot.duration_sec)  (±10% or ±2 s)
3. every character has name / description / reference_image that exists on disk
   and is at least 1024 px on its short edge (the H3 reference pipeline expects
   a real, single-view portrait; a 3-view sheet is rejected by warning)
4. every shot: scene_id resolves, characters_present resolve, duration <= 15 s,
   transition in {cut, chain, match_cut}
5. visual_prompt mentions every character in characters_present (by name or id)
6. dialogue tags are legal: <d>说话人：台词</d>, speaker must be one of the
   characters present (or listed in the project)

Usage
-----
    python scripts/validate_script.py drama_script.json
    python scripts/validate_script.py drama_script.json --json
    python scripts/validate_script.py drama_script.json --strict-resolution
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

REQUIRED_PROJECT = ("project_id", "title", "target_duration", "visual_style")
TRANSITIONS = {"cut", "chain", "match_cut"}
DIALOGUE_RE = re.compile(r"<d>\s*([^：:<>\n]{1,20})\s*[：:]\s*([^<>\n]{1,200}?)\s*</d>")
MAX_SHOT_SECONDS = 15.0
MIN_REF_SHORT_EDGE = 1024

# --- continuity / action-density heuristics --------------------------------
# Verbs that each demand their own screen time. A shot carrying too many of them
# is what produced the "umbrella teleports into her hand" artefact: the model
# renders the end state of each action and skips the transition.
ACTION_VERBS = (
    "push", "pull", "open", "close", "walk", "run", "turn", "sit", "stand",
    "draw", "sheathe", "drop", "jump", "slam", "lift", "raise", "lower",
    "slide", "reach", "grip", "grab", "pick", "place", "put",
    "tap", "cut", "strike", "pass", "enter", "exit", "shake",
    "wipe", "point", "write", "sign", "dial", "answer",
    "climb", "kneel", "fall", "spin", "dodge", "step", "check", "rise", "cross",
)
# coarse framing ladder; a [chain] across two steps or more has to re-frame and
# therefore has to morph instead of continuing the motion
FRAMING_TIERS = (
    ("extreme close", 4), ("macro", 4),
    ("close-up", 3), ("close shot", 3), ("close up", 3),
    ("medium", 2), ("mid shot", 2), ("two-shot", 2),
    ("wide", 1), ("full shot", 1), ("long shot", 1), ("establishing", 1),
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def image_size(path: Path) -> tuple[int, int] | None:
    """Read PNG/JPEG dimensions without Pillow."""
    try:
        with path.open("rb") as fh:
            head = fh.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                w, h = struct.unpack(">II", head[16:24])
                return int(w), int(h)
            if head[:2] == b"\xff\xd8":  # jpeg: walk segments
                fh.seek(2)
                while True:
                    marker = fh.read(2)
                    if len(marker) < 2:
                        return None
                    if marker[0] != 0xFF:
                        return None
                    m = marker[1]
                    if m in (0xC0, 0xC1, 0xC2, 0xC3):
                        fh.read(3)
                        h, w = struct.unpack(">HH", fh.read(4))
                        return int(w), int(h)
                    seg_len = struct.unpack(">H", fh.read(2))[0]
                    fh.seek(seg_len - 2, 1)
    except OSError:
        return None
    return None


def resolve_ref(ref: str, base_dir: Path) -> Path:
    """Resolve a reference path against the script dir, then the repo root."""
    p = Path(ref)
    if p.is_absolute():
        return p
    cand = (base_dir / p)
    if cand.exists():
        return cand
    repo = Path(__file__).resolve().parent.parent
    cand2 = repo / p
    return cand2 if cand2.exists() or p.parts[:1] == ("drama",) else cand


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.info.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def framing_tier(*texts: str) -> int | None:
    """Coarse shot size (1=wide … 4=extreme close-up), or None if unstated."""
    blob = " ".join(t for t in texts if t).lower()
    best = None
    for token, tier in FRAMING_TIERS:
        if token in blob:
            best = tier if best is None else max(best, tier)
    return best


def action_verbs(text: str) -> list[str]:
    """Distinct *character* actions in a visual prompt.

    A bare verb list over-counts badly ("her hand", "catching the light"), so a
    hit requires the verb to take an object or a direction:
        pushes the door / closes her umbrella / sits at the table / pulls into a smirk
    """
    low = re.sub(r"[^a-z0-9]+", " ", text.lower())
    tails = ("the", "a", "an", "her", "his", "their", "its", "down", "up", "out",
             "in", "on", "off", "over", "back", "to", "at", "from", "into", "toward")
    hits = []
    for v in ACTION_VERBS:
        forms = "|".join((v, v + "s", v + "es", v + "ed", v + "ing"))
        pat = rf"\b(?:{forms})\s+(?:{'|'.join(tails)})\b"
        if re.search(pat, low):
            hits.append(v)
    return sorted(set(hits))


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #
def validate(script: dict, base_dir: Path, strict_res: bool = False) -> Report:
    rep = Report()

    for key in REQUIRED_PROJECT:
        if script.get(key) in (None, "", []):
            rep.error(f"缺少项目字段: {key}")

    style = script.get("visual_style") or ""
    if style and not re.fullmatch(r"[A-Za-z0-9 _\-/,]+", str(style)):
        rep.warn(f"visual_style 建议用英文（当前: {style!r}）")

    chars = script.get("characters") or []
    scenes = script.get("scenes") or []
    shots = script.get("shots") or []

    if len(chars) < 1:
        rep.error("characters 为空：至少需要 1 个角色")
    if len(scenes) < 1:
        rep.error("scenes 为空：至少需要 1 个场景")
    if len(shots) < 1:
        rep.error("shots 为空：至少需要 1 个镜头")

    # ---- characters ----
    char_ids: set[str] = set()
    char_names: set[str] = set()
    for i, c in enumerate(chars):
        cid = str(c.get("character_id") or c.get("name") or f"char_{i}")
        name = str(c.get("name") or "").strip()
        if not name:
            rep.error(f"characters[{i}] 缺少 name")
        if cid in char_ids:
            rep.error(f"character_id 重复: {cid}")
        char_ids.add(cid)
        if name:
            char_names.add(name)

        desc = str(c.get("description") or "").strip()
        if not desc:
            rep.error(f"角色 {name or cid} 缺少 description（英文视觉描述）")
        elif not re.search(r"[A-Za-z]", desc):
            rep.warn(f"角色 {name or cid} 的 description 似乎不是英文: {desc[:40]!r}")

        ref = c.get("reference_image")
        if not ref:
            rep.error(f"角色 {name or cid} 缺少 reference_image")
            continue
        p = resolve_ref(str(ref), base_dir)
        if not p.is_file():
            rep.error(f"角色 {name or cid} 的 reference_image 不存在: {ref}")
            continue
        size = image_size(p)
        if size is None:
            rep.warn(f"角色 {name or cid} 的 reference_image 无法解析尺寸: {ref}")
            continue
        w, h = size
        short = min(w, h)
        if short < MIN_REF_SHORT_EDGE:
            msg = (f"角色 {name or cid} 参考图短边 {short}px < {MIN_REF_SHORT_EDGE}px "
                   f"({w}x{h})")
            (rep.error if strict_res else rep.warn)(msg)
        if w / max(h, 1) > 1.45:
            rep.error(f"角色 {name or cid} 参考图疑似多视图拼图（{w}x{h}，宽高比 {w/h:.2f}）"
                      f"：三视图会让 H3 识别成多个角色，请换成单视图")
        rep.note(f"角色 {name or cid}: {w}x{h} {'✓' if short >= MIN_REF_SHORT_EDGE else '⚠ 分辨率偏低'}")

    # ---- scenes ----
    scene_ids: set[str] = set()
    for i, s in enumerate(scenes):
        sid = str(s.get("scene_id") or "")
        if not sid:
            rep.error(f"scenes[{i}] 缺少 scene_id")
            continue
        if sid in scene_ids:
            rep.error(f"scene_id 重复: {sid}")
        scene_ids.add(sid)
        for field in ("location", "time_of_day", "atmosphere"):
            if not s.get(field):
                rep.warn(f"场景 {sid} 缺少 {field}（会削弱画面一致性）")
        ref = s.get("reference_image")
        if ref:
            p = resolve_ref(str(ref), base_dir)
            if not p.is_file():
                rep.error(f"场景 {sid} 的 reference_image 不存在: {ref}")

    # ---- shots ----
    total = 0.0
    seen_shots: set[str] = set()
    prev_label = None
    prev_present: set[str] = set()
    prev_tier: int | None = None
    for i, sh in enumerate(shots):
        label = sh.get("shot_id") or f"shots[{i}]"
        if not sh.get("shot_id"):
            rep.error(f"shots[{i}] 缺少 shot_id")
        elif sh["shot_id"] in seen_shots:
            rep.error(f"shot_id 重复: {sh['shot_id']}")
        seen_shots.add(str(sh.get("shot_id")))

        dur = sh.get("duration_sec")
        if not isinstance(dur, (int, float)) or dur <= 0:
            rep.error(f"{label}: duration_sec 非法 ({dur!r})")
        else:
            total += float(dur)
            if dur > MAX_SHOT_SECONDS:
                rep.error(f"{label}: duration_sec {dur} 超过 H3 单镜上限 {MAX_SHOT_SECONDS}s")

        sid = sh.get("scene_id")
        if sid not in scene_ids:
            rep.error(f"{label}: scene_id {sid!r} 不存在于 scenes")

        present = sh.get("characters_present") or []
        if not isinstance(present, list):
            rep.error(f"{label}: characters_present 必须是数组")
            present = []
        for who in present:
            if who not in char_ids and who not in char_names:
                rep.error(f"{label}: characters_present 中的 {who!r} 不是已定义角色")

        prompt = str(sh.get("visual_prompt") or "")
        if not prompt:
            rep.error(f"{label}: 缺少 visual_prompt")
        elif not re.search(r"[A-Za-z]", prompt):
            rep.warn(f"{label}: visual_prompt 建议用英文")
        for who in present:
            names = {who}
            for c in chars:
                if who in (c.get("character_id"), c.get("name")):
                    names |= {str(c.get("name") or ""), str(c.get("character_id") or "")}
            names = {n for n in names if n}
            if not any(n.lower() in prompt.lower() for n in names):
                rep.error(f"{label}: visual_prompt 未包含角色名 {who!r}"
                          f"（需要显式写出，否则模型不知道画面里有谁）")

        dialogue = str(sh.get("dialogue") or "")
        if dialogue:
            found = DIALOGUE_RE.findall(dialogue)
            if not found:
                rep.error(f"{label}: dialogue 存在但没有合法 <d>说话人：台词</d> 标签")
            leftover = DIALOGUE_RE.sub("", dialogue).strip()
            if leftover:
                rep.warn(f"{label}: dialogue 含 <d> 之外的文本: {leftover[:40]!r}")
            for speaker, line in found:
                if speaker not in char_names and speaker not in char_ids:
                    rep.error(f"{label}: 台词说话人 {speaker!r} 不是已定义角色")
                if len(line.strip()) < 1:
                    rep.error(f"{label}: 台词为空")

        trans = sh.get("transition", "cut")
        if trans not in TRANSITIONS:
            rep.error(f"{label}: transition {trans!r} 非法（允许 {sorted(TRANSITIONS)}）")

        cam = str(sh.get("camera") or "")
        if not cam:
            rep.warn(f"{label}: 缺少 camera（景别+运镜）")

        # ---- scene anchor -----------------------------------------------------
        anchor = str(sh.get("anchor") or "")
        scene_obj = next((x for x in scenes if str(x.get("scene_id")) == str(sid)), None)
        if anchor == "plate":
            imgs = (scene_obj or {}).get("images") or []
            if not imgs:
                rep.error(f"{label}: anchor=plate 但场景 {sid} 没有母版图（scenes[].images 为空）")
            if present:
                rep.warn(f"{label}: anchor=plate 会走 i2v，角色参考图不生效——"
                         f"建议只给空镜建立镜头用 anchor")

        # ---- continuity: a [chain] is a pixel-locked hand-off ----------------
        present_set = set(present)
        tier = framing_tier(cam, prompt)
        if trans == "chain":
            if prev_label is None:
                rep.error(f"{label}: 第一镜不能是 chain（没有可锁定的前一帧），请改成 cut")
            elif present_set != prev_present:
                added = sorted(present_set - prev_present)
                removed = sorted(prev_present - present_set)
                rep.error(
                    f"{label}: chain 但画面主体变了（新增 {added or '无'} / 移除 {removed or '无'}）。"
                    f"chain 会把上一镜末帧逐像素锁成首帧，换人只能在原地变形——请改成 cut"
                )
            elif prev_tier is not None and tier is not None and abs(tier - prev_tier) >= 2:
                rep.warn(
                    f"{label}: chain 但景别从 {prev_tier} 跳到 {tier}（≥2 档）。"
                    f"构图变化太大时模型仍会优先保住锁定帧，建议改 cut"
                )

        # ---- action density (ignore the camera clause: "slow push-in" is not
        #      something the character does) --------------------------------
        body = prompt.replace(cam, " ") if cam else prompt
        verbs = action_verbs(body)
        if len(verbs) >= 5:
            rep.error(
                f"{label}: 单镜有 {len(verbs)} 个动作 {verbs}。"
                f"模型只渲首尾两态，中间过程会跳变（收伞变魔术就是这么来的）——"
                f"请拆镜，或只留一个主要动作"
            )
        elif len(verbs) >= 3:
            rep.warn(
                f"{label}: 单镜有 {len(verbs)} 个动作 {verbs}，"
                f"建议压到 1–2 个；动作越多，过渡越容易被吞掉"
            )

        prev_label = label
        prev_present = present_set
        prev_tier = tier

    target = script.get("target_duration")
    if isinstance(target, (int, float)) and shots:
        diff = abs(float(target) - total)
        tol = max(2.0, 0.10 * float(target))
        if diff > tol:
            rep.error(f"target_duration={target}s 与镜头总和 {total:.1f}s 相差 {diff:.1f}s "
                      f"(容差 {tol:.1f}s)")
        else:
            rep.note(f"时长: 目标 {target}s / 实际 {total:.1f}s ✓")

    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate drama_script.json")
    ap.add_argument("script")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict-resolution", action="store_true",
                    help="treat <1024px reference images as errors")
    args = ap.parse_args(argv)

    path = Path(args.script).expanduser().resolve()
    if not path.is_file():
        print(f"脚本不存在: {path}", file=sys.stderr)
        return 2
    try:
        script = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"JSON 解析失败: {e}", file=sys.stderr)
        return 2

    rep = validate(script, path.parent, args.strict_resolution)

    if args.json:
        print(json.dumps({"ok": rep.ok, "errors": rep.errors,
                          "warnings": rep.warnings, "info": rep.info},
                         ensure_ascii=False, indent=2))
    else:
        print(f"== {path.name} ==")
        print(f"项目: {script.get('project_id')} · {script.get('title')} · "
              f"{script.get('visual_style')}")
        print(f"角色 {len(script.get('characters') or [])} · "
              f"场景 {len(script.get('scenes') or [])} · "
              f"镜头 {len(script.get('shots') or [])}")
        for line in rep.info:
            print(f"  ✓ {line}")
        for line in rep.warnings:
            print(f"  ⚠ {line}")
        for line in rep.errors:
            print(f"  ✗ {line}")
        print("结果:", "通过" if rep.ok else f"未通过（{len(rep.errors)} 个错误）")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())

