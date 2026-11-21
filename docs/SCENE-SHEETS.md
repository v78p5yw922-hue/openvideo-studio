# 场景母版规格（给 Trae 生成用）

> 用途：生成场景母版图 → 上传到工作台「场景卡」→ 渲染时作为空间参考（`anchor=plate` 时直接用作首帧）。
> 每个场景：**主母版 1 张 + 附加角度 1 张**（多给一张，模型才不会把空间压成一张平面）。

---

## 一、通用规格（所有场景）

| 项 | 要求 | 为什么 |
|---|---|---|
| **分辨率** | **1344×768**（16:9 附近，等于成片画幅） | 母版会直接当 `anchor=plate` 的首帧，比例不对会被拉伸 |
| **画幅内容** | **空景：没有人、没有动物、没有文字** | 有人的母版会让模型把那个人画进每一镜 |
| **三个距离层** | 前景（虚化遮挡）+ 中景（地面/主空间）+ 后景（建筑/深处） | 没有层的画面渲出来是"一张平面" |
| **可动元素 ≥2 个** | 灯笼会晃、火苗会跳、烟会飘、浮尘会翻、帘会动 | 背景不动 = 假 |
| **光源写死方位** | 「右前上方」「左后上方」这种，不要只写"暖光" | 同一场景不同镜头光线方向必须一致 |
| **材质具体** | 青石（湿润/干燥）、朱红漆木、粗麻布、铜锈 | 写"古风街道"模型只会给模板货 |
| **时间固定** | 同一个场景所有角度必须是**同一时刻** | 一张白天一张黄昏，模型会平均成四不像 |

### 负面词（每次都带上）

```
现代建筑、汽车、电线杆、塑料、霓虹灯、LED、文字、招牌字、水印、多人、人群、
动漫风格、插画、过度磨皮、广角畸变、鱼眼
```

### 三个角度怎么规划

镜头表里用到哪几种机位，母版就至少覆盖最常用的那种角度：

| 镜头里常见的角度 | 母版该给的角度 |
|---|---|
| `正面` / `反打` | **A. 主母版**（正对主空间） |
| `过肩-左/右` | **B. 反向母版**（从对面看回来，前景有虚化物件） |
| `侧对-左/右` | C. 侧向母版（可选，第三张） |
| `背拍-左后/右后` | A 的背面机位（可选） |

---

## 二、场景 1：庙会街（`temple_fair`）

### 空间设定

```
时间：白昼，晴，暖阳 3200K，光从右前上方来（低角度，有长影）
地面：湿润青石板路，砖缝泛青苔，有车轮压出的浅痕
左：红灯笼架 —— 木架高约 2.6 米，三层，每层挂 5 只红纱灯笼（灯笼会轻晃）
中：青石路向纵深延伸，远处是街市人流（虚化、不成形）
右：糖画摊 —— 木案、铜锅冒白汽、竹签扎成一把、糖丝挂成金色细丝
后景：两层木构楼阁，朱红廊柱，飞檐，檐下挂成排红灯笼
前景（虚化）：一角挑高的布幡 / 灯笼的下缘 / 糖画摊的竹架
可动元素：灯笼轻晃、铜锅白汽上升、布幡被风扯动、地面浮尘在光柱里翻
```

### A. 主母版（正面，用于 正面 / 反打 镜头）

```
Chinese Tang dynasty temple street fair, eye-level wide establishing shot, empty street, no people.
Left: a tall wooden lantern rack, three tiers, five red gauze lanterns per tier, lanterns swaying slightly.
Center: wet bluestone paving extending into depth, faint wheel ruts between the stones.
Right: a candy-figure stall with a wooden counter, a copper pot steaming, bamboo skewers in a bundle.
Background: two-storey timber buildings with vermilion columns and upturned eaves, rows of red lanterns
hanging under the eaves, distant crowd blurred into soft shapes.
Foreground: a blurred banner edge at the top-left corner, shallow depth of field.
Lighting: bright daylight, warm 3200K sun from the upper right front, long soft shadows across the stones.
Atmosphere: lively market, airborne dust motes caught in the light shafts.
Camera: 35mm, f/4, slight perspective, high detail stone and wood grain, cinematic photoreal, 1344x768.
Negative: modern buildings, cars, power lines, plastic, neon, text, signage, watermark, people, crowds.
```

### B. 反向母版（从街对面看回来，用于 过肩-左 镜头）

```
Same Tang dynasty temple street fair, seen from the opposite end of the street, eye-level medium shot,
empty street, no people.
Foreground: a row of red gauze lanterns and the wooden frame of the candy stall, heavily blurred.
Middle: the bluestone paving, now catching the sun from the left.
Background: the same two-storey timber buildings and vermilion columns, seen from the other side.
Lighting: same bright daylight, warm 3200K, now from the upper left front, lanterns forming warm
edges on both sides of the frame.
Must be the same location as the main plate: identical lantern arrangement, identical stone pattern.
Camera: 35mm, f/2.8, cinematic photoreal, 1344x768.
Negative: modern elements, text, watermark, people, different time of day.
```

---

## 三、场景 2：地下宫殿（`underground_palace`）

### 空间设定

```
时间：无自然光，只有火把
结构：中轴对称，两侧巨型青石立柱（柱身云雷纹浮雕），中间石阶向上，顶部拱形石券顶
左：石柱一列，柱基有青苔
中：石阶，阶面磨得发亮，缝隙积灰
右：火把架（三支铁架，各插一支火把，火苗会跳，烟往上飘）
后景：石壁刻暗金铭文，深处沉入黑
前景（虚化）：一根立柱的边角 / 飘起的烟
可动元素：火苗跳动、烟斜着飘、石屑从拱顶落下、浮尘在火光里翻
```

### A. 主母版（中轴全景，用于 正面 镜头）

```
Ancient Chinese underground tomb palace, symmetrical axial wide shot, empty, no people.
Two rows of massive bluestone columns with carved thunder-pattern reliefs, a broad flight of stone
steps rising into darkness in the center, barrel-vaulted stone ceiling above.
Left: a column base with moss in the joints. Right: an iron torch stand with three torches, flames
flickering, smoke drifting upward at an angle.
Background: walls covered in faint gold-inlaid inscriptions, fading into black.
Foreground: the blurred edge of a column at the frame edge.
Lighting: torchlight only, warm 2800K, hard rim light on the columns, deep shadows elsewhere,
volumetric haze and drifting dust.
Camera: 24mm, f/5.6, deep focus, cinematic photoreal, 1344x768.
Negative: modern architecture, concrete, metal railings, LED, text, watermark, people.
```

### B. 反向母版（从石阶上方往下来，用于 反打 / 背拍 镜头）

```
Same underground tomb palace, camera placed at the top of the stone steps looking back down,
empty, no people.
Foreground: the edge of the upper step, slightly out of focus.
Middle: the flight of steps descending, worn and glossy.
Background: the two rows of carved columns receding, torch stands glowing at the sides, darkness beyond.
Lighting: same torchlight 2800K, flames behind the columns creating rim light, haze in the air.
Must match the main plate: same stone colour, same column carving, same torch positions.
Camera: 35mm, f/4, cinematic photoreal, 1344x768.
Negative: modern elements, text, watermark, people.
```

---

## 四、上传方式

```
工作台 → 场景（左栏）→ 选场景
  「母版」→ 上传 A（第一张）
  「追加视角」→ 上传 B（和可选的 C）
```

渲染时：

| 情况 | 用哪张 |
|---|---|
| 镜头 `锚定=plate`（空镜 / 已设首帧母版） | A 直接当**首帧** |
| 普通人物镜 | A + B 一起作为**空间参考**（多一张，模型才不会把空间压成平面） |

---

## 五、验收清单

```
□ 分辨率正好 1344×768
□ 画面里没有人（有人 = 那个人的脸会被画进每一镜）
□ 有前景虚化遮挡（没有 = 画面扁平）
□ 至少 2 个会动的东西（灯笼/火苗/烟/尘）
□ 光的方向写死了（右前上方 / 左前上方）
□ 同一场景 A 和 B 看起来是同一个地方（同样的石材、同样的灯笼排布）
□ 没有文字、招牌、水印
```

---

## 六、交付给 Trae 的说法

```
请生成两组场景母版，每组 2 张（A 主角度 + B 反方向），共 4 张：

1. 庙会街：唐代街市、白昼暖阳（右前上方 3200K）、左红灯笼架、中青石路、右糖画摊
2. 地下宫殿：中轴对称陵宫、只有火把光（2800K）、两侧青石立柱、中间石阶、右火把架

硬要求：
- 1344×768，画面里必须没有人
- 必须有前景虚化遮挡 + 至少 2 个会动的元素
- 同一场景的 A/B 必须是"同一个地方"的不同机位（同样的石材、同样的物件排布）
- 不要文字、招牌、水印、现代元素

详细 prompt 见 SCENE-SHEETS.md。
```
