"""MV 模块：把「一段音乐 + 一个地域/舞蹈风格」展开成可直接渲染的分镜项目。

思路：不新增渲染管线 —— MV 就是"音乐 + 一组 5/8 秒镜头"，
      这里生成一份 V3 分镜 Markdown，走既有的导入 / 校验 / 渲染 / 合成流程。
"""

from __future__ import annotations

import math
import uuid

PRESETS: list[dict] = [
    # —— 风景类（中国地域）
    {
        "id": "bj_hutong", "kind": "scenery", "region": "北京",
        "label": "北京·胡同清晨", "zh": "灰砖院墙、槐树影子、自行车铃、晨雾里的早点摊",
        "en": ("Beijing hutong at dawn, grey brick courtyard walls, locust tree shadows, "
               "a bicycle bell, steam from a breakfast stall, soft golden morning light"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
    {
        "id": "nm_grass", "kind": "scenery", "region": "内蒙",
        "label": "内蒙·草原黄昏", "zh": "起伏草浪、远处羊群、风把经幡吹直、橙红晚霞压到地平线",
        "en": ("Inner Mongolia grassland at dusk, rolling grass, distant flock of sheep, "
               "prayer flags pulled straight by wind, orange-red afterglow on the horizon"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
    {
        "id": "sz_night", "kind": "scenery", "region": "深圳",
        "label": "深圳·夜色天际线", "zh": "玻璃幕墙、霓虹倒影、车流拉出光带、海风把云推快",
        "en": ("Shenzhen skyline at night, glass curtain walls, neon reflections, "
               "traffic light trails, clouds pushed fast by sea wind"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
    # —— 舞蹈类
    {
        "id": "dance_shuixiu", "kind": "dance", "region": "中国",
        "label": "国风·水袖", "zh": "青砖庭院、水袖甩出弧线、慢镜头、脚步贴着地面碾",
        "en": ("Chinese classical dance, long white water sleeves tracing arcs, "
               "grey brick courtyard, slow motion, feet pivoting on stone floor"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
    {
        "id": "dance_hiphop", "kind": "dance", "region": "中国",
        "label": "街舞·天台", "zh": "城市天台、逆光、汗珠、快速的脚步与定格",
        "en": ("street dance on a city rooftop, backlit by low sun, sweat droplets, "
               "fast footwork and sharp freeze poses"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
    {
        "id": "dance_ballet", "kind": "dance", "region": "西方",
        "label": "芭蕾·练功房", "zh": "把杆、晨光斜切、粉尘在光里翻、脚尖绷直",
        "en": ("ballet studio, wooden barre, morning light cutting across the room, "
               "dust motes in the beam, pointed feet, mirror wall"),
        "style": "2D anime, cel-shaded, clean line art, limited palette, hand-painted background",
    },
]

# 镜头模板：按序号轮换机位与动作，保证不"幻灯片"
_CAM = [
    ("全景", "平视", "正面", "固定", 5.0),
    ("中景", "略俯", "侧对-左", "横移", 2.6),
    ("近景", "平视", "侧对-右", "缓推", 1.4),
    ("中全", "略仰", "正面", "缓拉", 3.0),
    ("特写", "略俯", "侧对-左", "固定", 0.9),
    ("中景", "平视", "正面", "跟拍", 2.2),
]

_SCENERY_BEATS = [
    "0.0 光线把 {where} 照出层次、空气里有浮尘 | 1.6 一阵风把最外层的轮廓推动 | 3.2 一只鸟从画面深处掠过 | 4.4 镜头继续往前、边缘景物流过",
    "0.0 {where} 的细节被推近、纹理清楚 | 1.8 光在表面移动一格 | 3.4 有细小的东西被风带起 | 4.6 影子扫过画面",
    "0.0 画面停在一个小物件上、它是干净的 | 1.5 背景里有人/车经过、被虚化 | 3.0 物件轻微晃动 | 4.2 镜头开始离开它",
    "0.0 {where} 的全貌展开、前中后景分层 | 2.0 云影从画面右侧压过来 | 3.6 一群鸟/车流穿过中景 | 4.8 光重新亮起来",
    "0.0 极近的质感、能看到颗粒 | 1.6 一滴水/一粒沙落下来 | 3.2 光斑滑过 | 4.4 焦点开始向后拉",
]

_DANCE_BEATS = [
    "0.0 她/他站定、重心落到前脚掌 | 1.4 手臂甩出去、袖口/衣角带风 | 2.8 转身、头发甩起 | 4.0 落地、膝盖缓冲",
    "0.0 脚步贴地碾过、鞋底与地面擦出细响 | 1.6 上身反方向拧 | 3.0 手臂从下往上穿过身体侧面 | 4.4 定格两拍",
    "0.0 逆光里的轮廓、汗珠亮起来 | 1.5 一个快速的连续步 | 3.0 身体折下去又弹起来 | 4.2 眼神扫向镜头",
    "0.0 起跳、离地 | 1.2 空中转体、衣摆张开 | 2.6 落地、膝盖下沉 | 4.0 起身、肩膀先动",
    "0.0 极近的手部/脚部特写 | 1.4 肌肉绷起又放松 | 2.8 衣物褶皱移动 | 4.2 镜头拉远、露出全身",
]


def list_presets() -> list[dict]:
    return PRESETS


def get_preset(pid: str) -> dict | None:
    return next((p for p in PRESETS if p["id"] == pid), None)


def plan_shots(duration: float, preset: dict) -> list[dict]:
    """按音乐时长切镜头：每镜 5 或 8 秒，镜头数 3–10。"""
    n = max(3, min(10, int(round(duration / 6.5))))
    per = 5 if duration / n <= 5.6 else 8
    beats = _DANCE_BEATS if preset["kind"] == "dance" else _SCENERY_BEATS
    shots = []
    for i in range(n):
        cam = _CAM[i % len(_CAM)]
        beat = beats[i % len(beats)].replace("{where}", preset["label"].split("·")[-1])
        shots.append({"index": i + 1, "seconds": per, "camera": cam, "beat": beat})
    return shots


def build_markdown(preset: dict, music: dict, duration: float) -> tuple[str, str]:
    """生成一份可导入的 V3 分镜；返回 (标题, markdown)。"""
    shots = plan_shots(duration, preset)
    title = f"MV·{preset['label']}"
    sc_id = "mv_" + preset["id"]
    scene_name = preset["label"].split("·")[-1]
    cast = "舞者" if preset["kind"] == "dance" else ""

    lines: list[str] = []
    lines.append("## 项目\n")
    lines.append(f"- 片名：{title}")
    lines.append(f"- 时间地点：{preset['region']} · {scene_name}")
    lines.append(f"- 总时长：{sum(s['seconds'] for s in shots)} 秒 / {len(shots)} 镜")
    lines.append(f"- 一句话故事：{preset['zh']}")
    vocal = "带人声演唱" if music.get("lyrics") else "纯器乐"
    lines.append(f"- 说明：MV 草稿（音乐《{music.get('name') or music.get('id')}》，{duration:.0f} 秒，{vocal}）。"
                 f"整片只用这一首音乐铺底：镜头之间不重新起音乐，转场处保持同一段旋律延续；"
                 f"镜头用末帧接续（chain）串起来，空间与光线从头到尾不换。\n")

    lines.append("## 世界设定\n")
    lines.append(f"- 视觉语言：{preset['style']}\n")

    if cast:
        lines.append("## 角色卡\n")
        lines.append(f"### {cast}\n")
        lines.append("- 年龄 / 身份：20 岁出头 / 舞者")
        lines.append("- 一句话人设：动作干净，眼神稳")
        lines.append("- 外观描述：A young Chinese dancer, lean and athletic, short black hair, "
                     "plain dark practice clothes, clean silhouette.")
        lines.append("- 一致性锁：短黑发、深色练功服、干净轮廓")
        lines.append("- 步态锁：重心低、落地先脚尖后脚跟\n")

    lines.append("## 场景卡\n")
    lines.append(f"### 场景 1：{scene_name}（id：{sc_id}）\n")
    lines.append(f"- 机位：A 全景 5.0 米；B 中景 2.6 米；C 近景 1.4 米")
    lines.append(f"- 固定物件：{preset['zh']}")
    lines.append(f"- 光源：{preset['en'].split(',')[-1].strip()}")
    lines.append("- 母版镜头号：镜 1\n")

    lines.append("## 镜头表\n")
    lines.append("| 镜号 | 场景 | 出场角色 | 机位 | 动作 | 台词 | 音效 | 配乐 | 转场 | 锚定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for s in shots:
        g, h, a, m, d = s["camera"]
        cast_cell = cast or "空镜"
        trans = "cut" if s["index"] == 1 else "chain"
        lines.append(f"| 镜 {s['index']}（{s['seconds']}s） | {scene_name} | {cast_cell} | "
                     f"{g}+{h}+{a}+{m}，{d}米 | {preset['label']} | 无 | 环境声随画面 | "
                     f"一首音乐到底 | {trans} | 空 |")
    lines.append("")

    lines.append("## 每镜 prompt\n")
    for s in shots:
        g, h, a, m, d = s["camera"]
        lines.append(f"### 镜 {s['index']}\n")
        lines.append(f"【机位】景别={g}；高度={h}；角度={a}；运镜={m}；距离={d}")
        lines.append("【接续】无" if s["index"] == 1 else "【接续】接上一镜末帧（同机位、同光线，只延续动作）")
        if cast:
            lines.append("【人物】")
            pos = "中" if s["index"] % 2 else "右"
            lines.append(f"- {cast}：位置={pos}；层级=中景；朝向=面向镜头；视线=看画外；占比=48")
            lines.append("【语气】无")
            lines.append("【开口】0.0")
        lines.append(f"【节奏】{s['beat']}")
        lines.append(f"【场景】同一处空间（固定锚点，每镜都不换）：{preset['zh']}；{preset['en']}；"
                     f"画面里始终是这一处地方，墙/街/地平线的位置不变")
        if cast:
            lines.append(f"【人物】里只有 {cast}，全程都在画面内，不出现第二个人")
        lines.append("【画质】赛璐璐上色，线稿干净，平涂两档阴影，背景手绘，2D 动画\n")
    return title, "\n".join(lines)
