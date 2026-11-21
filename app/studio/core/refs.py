"""参考图槽位预算（slot budget）—— 架构层，所有后端共用。

## 为什么要有这一层

H3 的 `ref_images` 一共 **9 个槽**，人物图和场景图**共享**这 9 个槽
（`ComfyUI-H3-Cast/cast.py: MAX_IMAGES = 9`，官方 tooltip 也这么写）。
节点按"人物在前、场景在后"分配，槽满了就**静默丢弃**：
场景图整张丢、多余视角丢、极端情况下整个角色被跳过（`member skipped entirely`）。

旧实现是 `refs[:4]` 硬取：三人镜 3×4=12 > 9，必然丢场景图 —— 模型于是自己编房间。

## 设计

1. `CAPABILITIES`：**后端能力声明**。换模型（H3 cast / Ref2VA / 未来的关键帧模型）
   只改这张表，上层逻辑不变。
2. `plan_refs()`：**按镜头内容算槽位**，不靠人配。优先级是显式的、可测试的：
      末帧（连续性） > 场景主图 > 每个角色 1 张 > 角色第 2/3 张 > 场景附加 > 剩余
3. 计划**落库**（`shots.ref_plan_json`）并**进指纹**：换图/换选法 → 片段必然 stale。
4. 渲染器只消费计划，不再自己决定用几张图（杜绝 `refs[:4]` 这类隐式规则）。

## 视图标签

参考图的默认顺序约定为 `front, side, back, extra`（角色图集上传顺序）。
将来给每张图加标签时，只需给 `Character.ref_views` 填 `["front","side","back"]`，
本模块的 `_ordered_views()` 会自动用标签，不需要改调用方。
"""

from __future__ import annotations

from typing import Any

PLAN_VERSION = 1

# ---------------------------------------------------------------- 后端能力
# max_slots              : 一次推理最多几张参考图（H3 = 9）
# max_members            : 最多几个"可锁脸"的角色槽（H3 cast_1..3 = 3）
# max_views_per_member   : 每个角色最多几张视角（H3CastMember image_1..4 = 4）
# max_scene_views        : 场景最多几张（母版 + 附加，H3 = 4）
# supports_chain_frame   : 能否把上一镜末帧当参考（只有人物镜的近似方案）
CAPABILITIES: dict[str, dict] = {
    "h3_cast": {"max_slots": 9, "max_members": 3, "max_views_per_member": 4,
                "max_scene_views": 4, "supports_chain_frame": True},
    "h3_ref2va": {"max_slots": 9, "max_members": 9, "max_views_per_member": 9,
                  "max_scene_views": 4, "supports_chain_frame": False},
}

DEFAULT_BACKEND = "h3_cast"

# 景别 → 背景的重要程度。特写里背景只是虚化，不值得占一个槽。
SCENE_NEED = {"大特写": 0, "特写": 0, "近景": 1, "中景": 1,
              "中全": 2, "全景": 2, "远景": 2}

# 朝向 → 最有信息量的视角顺序（第一个是"主角视角"）
FACING_PREFERENCE = {
    "面向镜头": ("front", "side", "back", "extra"),
    "背对镜头": ("back", "side", "front", "extra"),
    "左3-4侧": ("side", "front", "back", "extra"),
    "右3-4侧": ("side", "front", "back", "extra"),
    "纯左侧": ("side", "back", "front", "extra"),
    "纯右侧": ("side", "back", "front", "extra"),
    "仰视镜头": ("front", "side", "back", "extra"),
}
DEFAULT_VIEWS = ("front", "side", "back", "extra")


def _ordered_views(member: dict) -> list[str]:
    """按"视角标签"给这个角色的图排序；没标签就按上传顺序。"""
    refs = [str(p) for p in (member.get("refs") or []) if p]
    if not refs:
        return []
    labels = member.get("ref_views") or []
    if len(labels) == len(refs):
        pref = FACING_PREFERENCE.get(str(member.get("facing") or ""), DEFAULT_VIEWS)
        pairs = list(zip(labels, refs))
        out = []
        for want in pref:
            out += [p for lab, p in pairs if lab == want and p not in out]
        out += [p for _, p in pairs if p not in out]
        return out
    return refs


def plan_refs(shot: dict, cast: list[dict], scene: dict | None,
              backend: str = DEFAULT_BACKEND) -> dict:
    """给一镜算出"用哪几张图、按什么顺序、丢了什么"。

    `cast` 每项：{character_id, name, refs[], ref_views?, facing?, share?}
    `share` = 该角色在画面里的占比（用于决定谁先拿槽），缺失按出场顺序。
    返回结构稳定（可 JSON 存库、可进指纹、可给 UI 展示）。
    """
    caps = CAPABILITIES.get(backend) or CAPABILITIES[DEFAULT_BACKEND]
    cap = int(caps["max_slots"])
    cam = shot.get("camera_slots") or {}
    shot_size = str(cam.get("景别") or "")
    need_scene = SCENE_NEED.get(shot_size, 1)

    members = [m for m in cast if (m.get("refs") or [])][: int(caps["max_members"])]
    # 谁的占比大，谁先拿槽（同占比按出场顺序，保证确定性）
    members.sort(key=lambda m: (-float(m.get("share") or 0), str(m.get("name") or "")))

    scene_views: list[str] = []
    if scene:
        plate = str(scene.get("plate_path") or "")
        extra = [str(x) for x in (scene.get("extra_plates") or []) if x]
        if plate:
            scene_views.append(plate)
        scene_views += extra
    scene_views = scene_views[: int(caps["max_scene_views"])]

    slots: list[dict] = []
    dropped: list[dict] = []
    warnings: list[str] = []

    def take(kind: str, path: str, owner: str = "", role: str = "", why: str = "") -> bool:
        if len(slots) >= cap:
            dropped.append({"kind": kind, "owner": owner, "path": path,
                            "role": role, "reason": f"{cap}-slot cap"})
            return False
        slots.append({"index": len(slots) + 1, "kind": kind, "owner": owner,
                      "name": owner, "role": role, "path": path, "why": why})
        return True

    # ① 末帧：连续性最重要（丢了会跳脸/跳景）
    if shot.get("chain_ref") and members and caps.get("supports_chain_frame"):
        take("frame", "__prev_last_frame__", owner="", role="continuity",
             why="接上一镜末帧")

    # ② 场景主图：中全/全景必须有，否则模型自己编房间
    scene_used = 0
    if scene_views and need_scene >= 1:
        if take("scene", scene_views[0], owner="scene", role="plate", why="场景母版"):
            scene_used = 1

    # ③ 每个角色先给 1 张（按朝向选最有用的一张）
    ordered = {id(m): _ordered_views(m) for m in members}
    for m in members:
        views = ordered[id(m)]
        if views:
            take("cast", views[0], owner=str(m.get("name") or ""),
                 role="primary-view", why="角色主视角")

    # ④ 每个角色第 2、3…张（整轮给，保证公平；给不齐就整轮跳过，
    #    把剩下的槽留给场景附加 / 末帧，而不是让排在前面的角色吃光）
    for depth in range(1, int(caps["max_views_per_member"])):
        wanting = [m for m in members if depth < len(ordered[id(m)])]
        if not wanting:
            break
        if cap - len(slots) < len(wanting):
            break
        for m in wanting:
            take("cast", ordered[id(m)][depth], owner=str(m.get("name") or ""),
                 role=f"view-{depth + 1}", why="补充视角")

    # ⑤ 场景附加视角：只有中全/全景且还有空位时才给
    for extra in scene_views[1:]:
        if need_scene >= 2 and len(slots) < cap:
            take("scene", extra, owner="scene", role="alt", why="场景附加视角")

    if not members and scene_views and scene_used == 0:
        warnings.append("这一镜只用到场景图，人物没有任何参考图")

    total_cast = sum(1 for s in slots if s["kind"] == "cast")
    if len(members) >= 3 and any(len(ordered[id(m)]) >= 3 for m in members):
        warnings.append(
            "三人同框：每人最多 2 张视角才能给场景图和末帧留位，多余的图会被丢掉"
        )

    usage = {
        "backend": backend,
        "cap": cap,
        "total": len(slots),
        "cast": total_cast,
        "scene": sum(1 for s in slots if s["kind"] == "scene"),
        "frame": sum(1 for s in slots if s["kind"] == "frame"),
        "dropped": len(dropped),
    }
    return {"version": PLAN_VERSION, "slots": slots, "dropped": dropped,
            "usage": usage, "warnings": warnings}


def plan_signature(plan: dict) -> list[list[str]]:
    """进指纹用：槽位顺序 + 文件路径（换图/换顺序都要失效）。"""
    return [[s["kind"], s["role"], s["path"]] for s in (plan.get("slots") or [])]


def plan_summary(plan: dict) -> str:
    """给 UI/日志的一行摘要。"""
    u = plan.get("usage") or {}
    bits = [f"{u.get('total')}/{u.get('cap')} 槽",
            f"人物 {u.get('cast')}",
            f"场景 {u.get('scene')}"]
    if u.get("frame"):
        bits.append(f"末帧 {u.get('frame')}")
    if u.get("dropped"):
        bits.append(f"⚠️ 丢 {u.get('dropped')} 张")
    return " · ".join(bits)
