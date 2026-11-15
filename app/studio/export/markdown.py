"""Export the project back to the Markdown script format we import.

The loop matters: you edit in DeepSeek, paste back, and nothing is lost —
shot ids come from 镜号, scene/cast ids are derived from names, so they stay
stable across a round-trip.
"""

from __future__ import annotations

from ..core import service


def build_markdown(project_id: str) -> str:
    project = service.get_project(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")
    shots = service.recompute_fingerprints(project_id)
    scenes = service.list_scenes(project_id)
    chars = {c["character_id"]: c for c in service.list_characters()}

    scene_order = {s["scene_id"]: i for i, s in enumerate(scenes)}
    out: list[str] = []

    out.append("## 项目")
    out.append(f"- 片名：{project['title']}")
    total = round(sum(float(s["duration"] or 5) for s in shots), 1)
    out.append(f"- 总时长：{total:g} 秒（共 {len(shots)} 镜）")
    out.append(f"- 空间数量：{len(scenes)} 个")
    if project.get("style"):
        out.append("")
        out.append("## 世界设定")
        out.append(f"- 视觉语言：{project['style']}")
    out.append("")

    out.append("## 角色卡")
    used = []
    for sh in shots:
        for cid in sh["cast"]:
            if cid in chars and cid not in used:
                used.append(cid)
    for cid in used:
        c = chars[cid]
        out.append(f"- 名字：{c['name']}")
        out.append(f"- 英文外观描述：{c.get('description', '')}")
        out.append("")
    if not used:
        out.append("（无角色）")
        out.append("")

    out.append("## 场景卡")
    for i, sc in enumerate(scenes, start=1):
        out.append(f"### 场景 {i}：{sc.get('name') or sc['scene_id']}（id：{sc['scene_id']}）")
        out.append(f"- 机位：{sc.get('camera', '')}")
        out.append(f"- 科技物件：{sc.get('props', '')}")
        out.append(f"- 光源：{sc.get('light', '')}")
        out.append("")

    out.append("## 镜头表")
    out.append("| 镜号 | 场景 | 出场角色（≤3） | 景别 / 机位 / 运动 | 空间锚点 | 动作 | 台词 | 转场 | 锚定 |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    scene_name = {s["scene_id"]: (s.get("name") or s["scene_id"]) for s in scenes}
    for sh in shots:
        n = int(sh["shot_id"].lstrip("s") or 0) or 1
        cast = "、".join(chars[c]["name"] for c in sh["cast"] if c in chars) or "无（空镜）"
        dialogue = sh.get("dialogue") or "无"
        anchor = "plate" if str(sh.get("anchor") or "") == "plate" else "空"
        out.append(
            f"| 镜 {n}（{float(sh['duration'] or 5):g}s） | {scene_name.get(sh['scene_id'], sh['scene_id'])} "
            f"| {cast} | {sh.get('camera', '')} | {sh.get('camera', '')} | "
            f"{sh.get('prompt', '')[:40]} | {dialogue} | {sh.get('transition', 'cut')} | {anchor} |")
    out.append("")

    out.append("## 每镜可执行 prompt")
    for sh in shots:
        n = int(sh["shot_id"].lstrip("s") or 0) or 1
        out.append(f"### 镜 {n}")
        out.append(sh.get("prompt", ""))
        out.append("")

    return "\n".join(out).rstrip() + "\n"
