# 人物定妆照规格（给 Trae 生成用）

> 用途：生成三个视图的定妆照 → 上传到工作台「角色卡」→ 渲染时作为角色参考图。
> 每个角色 **最多 3 张**（正 / 侧 3/4 / 背），全项目参考图总共 **9 个槽位**。

---

## 生成技术规格（所有角色通用）

| 项 | 要求 |
|---|---|
| 画幅 | 竖构图 3:4（如 1152×1536），人物全身入画、头顶留 8% 余量 |
| 背景 | 纯浅灰或米白无缝背景，**不要场景、不要道具** |
| 光 | 柔和均匀的主光 + 弱补光，色温 3200K（暖），不要硬阴影 |
| 镜头 | 50mm 等效，轻微透视，不要广角变形 |
| 表情 | 中性偏「角色性格」的一瞬（见各角色说明），**不要大笑不要哭** |
| 服装 | 三张必须是**同一套**，褶皱走向一致，不能换色 |
| 禁止 | 文字、水印、边框、多人、手部特写遮挡脸部 |
| 输出 | PNG，长边 ≥ 1536 |

**三张视图的要求**

1. **正面**：正对镜头，双手自然垂放，能看清领口、腰带、鞋
2. **侧 3/4**：身体转 45°，脸略朝镜头，能看到发式侧面与衣摆走向
3. **背面**：背对镜头，能看清后领、发式后侧、衣摆与腰带背面

---

## 林婉儿（22 岁 · 威远镖局总镖头独女）

### 人设一句话

**性格外放，衣着飘仙**——说话直、眼神不躲、站姿松弛张扬，但穿得轻纱广袖、走起来衣袂飞扬。

**这种反差要在同一张图里同时成立**：衣服是仙的，人是飒的。

### 外形要点

| 维度 | 内容 |
|---|---|
| 骨相 | 鹅蛋脸偏圆润，下颌线清晰，**右颊一道浅旧疤**（约 1.5cm，斜向） |
| 眉眼 | 眼型偏长、眼尾微挑，眼神**直视不躲**；眉毛自然浓、眉尾上扬 |
| 发式 | **高马尾**，用**红发带**扎起，马尾落在肩胛骨位置；鬓角留两缕碎发 |
| 体态 | 肩打开、下巴微抬，重心偏一侧腿（松弛但有力），一只手常扶着腰侧 |
| 表情 | 嘴角一侧先起笑，眼睛比嘴先笑——明快、有点挑衅 |
| 衣着 | 月白与青瓷色双层轻纱：外层**青色薄纱大袖**（透光、见纹理），内层月白交领长裙，**腰间细带垂两条长飘带**（及膝下），裙摆及地 |
| 鞋 | 白色软底绣鞋（正面必须露出来） |
| 质感关键词 | 纱料透光、袖口薄、飘带轻、腰带细 |

### 生图 prompt（英文，三张分别用）

**正面**

```
full-body character reference, front view, 22-year-old Chinese woman, oval face with a clear jaw,
a small pale diagonal scar on her right cheek, long narrow eyes slightly upturned, direct confident
gaze, thick natural eyebrows with raised tails, high ponytail tied with a red ribbon falling to her
shoulder blades, two loose strands at the temples, chin slightly lifted, one hand resting at her
waist, open relaxed stance.
Wearing a layered hanfu: moon-white cross-collar inner robe, a translucent celadon gauze outer robe
with very wide sleeves, thin waist sash with two long ribbons hanging past the knee, ivory skirt
brushing the ground, white soft embroidered shoes.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body in frame,
neutral confident half-smile, no text, no props, photorealistic, 3:4 vertical.
```

**侧 3/4**

```
same woman, same outfit, three-quarter view turned 45 degrees, face angled back toward camera,
visible ponytail with red ribbon from the side, translucent celadon gauze sleeve catching light,
ribbon ends swinging slightly, calm confident expression.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body, 3:4 vertical.
```

**背面**

```
same woman, same outfit, back view, showing the ponytail with red ribbon from behind, the back
collar of the moon-white robe, the drape of the translucent gauze outer robe, and the two long
waist ribbons hanging down, hands relaxed at her sides.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body, 3:4 vertical.
```

### 一致性锁（三张必须一致）

```
高马尾 + 红发带 · 右颊浅疤 · 青色薄纱大袖 · 腰间两条长飘带 · 白软底绣鞋
```

---

## 叶一恒（24 岁 · 户部尚书家三公子）

### 人设一句话

**衣着柔美，性格坚韧**——穿得月白软缎、竹纹暗绣、宽袖飘逸，但脊背笔直、眼神定得住、下颌收得紧。

**反差**：衣服软，人不软。

### 外形要点

| 维度 | 内容 |
|---|---|
| 骨相 | 清瘦长脸，颧骨不明显，**下颌线紧**（咬牙时更明显），喉结清晰 |
| 眉眼 | **丹凤眼**（眼尾细长上挑），**右眉尾一颗小痣**；眼神平稳不闪，看人时不眨眼 |
| 发式 | 乌发全部束起，**玉冠束发**，鬓角梳净，无刘海 |
| 体态 | 站姿笔直，双肩平，手里常持一把折扇；紧张时**抿嘴**、指节泛白 |
| 表情 | 表面温和，眼底沉着；不笑时嘴角平，笑时只嘴角微抬一点 |
| 衣着 | 月白（近白微暖）**软缎交领长衫**，**宽大垂袖**（袖口及手背），衣身**银灰竹纹暗绣**（不抢眼），腰束浅灰丝绦，内领浅灰，白袜黑布靴 |
| 质感关键词 | 缎面柔光、袖口垂坠、暗纹只在光下可见、腰带平滑 |

### 生图 prompt（英文，三张分别用）

**正面**

```
full-body character reference, front view, 24-year-old Chinese young man, lean long face,
tight jawline, prominent Adam's apple, narrow phoenix eyes with upturned tails, a tiny mole at the
tail of his right eyebrow, steady unblinking gaze, straight upright posture, shoulders level,
a folded paper fan held in one hand.
Black hair fully gathered into a neat topknot with a pale jade crown, temples combed back, no fringe.
Wearing a moon-white soft satin cross-collar changshan with very wide draping sleeves reaching the
back of the hands, subtle silver-grey bamboo embroidery on the body, a pale grey silk sash at the
waist, grey inner collar, white socks, black cloth boots.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body in frame,
calm composed expression, no text, no props other than the fan, photorealistic, 3:4 vertical.
```

**侧 3/4**

```
same young man, same outfit, three-quarter view turned 45 degrees, face angled toward camera,
visible topknot and jade crown from the side, the draping sleeve hanging in a soft fold,
upright posture, composed expression.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body, 3:4 vertical.
```

**背面**

```
same young man, same outfit, back view, showing the topknot and jade crown from behind, the back
collar of the moon-white satin robe, the long straight fall of the fabric, the pale grey sash tied
at the back, the wide sleeves hanging, hands relaxed at his sides.
Soft even studio light 3200K, plain pale grey seamless background, 50mm lens, full body, 3:4 vertical.
```

### 一致性锁（三张必须一致）

```
玉冠束发 · 右眉尾小痣 · 月白软缎 · 银灰竹纹暗绣 · 宽垂袖 · 浅灰丝绦 · 折扇
```

---

## 上传方式

```
工作台 → 角色（左栏）→ 选角色 → 「参考图」→ 一次上传 3 张
第 1 张会被当作主参考（正面优先）
上传后点「已验证」，校验器就不再提示缺图
```

**槽位预算**（上限 9）

| 用途 | 占用 |
|---|---|
| 叶一恒 3 张 | 3 |
| 林婉儿 3 张 | 3 |
| 场景母版 1 张 | 1 |
| 剩余 | 2（可留给第三个角色或备用角度） |

⚠️ **三人同框的镜头**（如 s05）会吃满 9 个槽位：3 人 × 每人最多 3 张 = 9，场景就没了。
这种镜头建议每人只用 2 张（正 + 侧），给场景留 1 张。

---

## 交付给 Trae 时的说法

```
请按下面两份规格各生成 3 张定妆照（正/侧3-4/背）：
1. 林婉儿：性格外放（直视、下巴微抬、一侧先起笑）+ 衣着飘仙（青色薄纱大袖、月白长裙、两条长飘带）
2. 叶一恒：衣着柔美（月白软缎、竹纹暗绣、宽垂袖）+ 性格坚韧（脊背直、下颌紧、眼神定）

要求：
- 3:4 竖构图，全身入画，纯浅灰背景，3200K 柔光
- 同一角色三张必须同一套服装、同一发式
- 不要文字水印，不要多人
- 一致性锁见规格末尾，三张都要满足

生成后存为 PNG，长边 ≥1536。
```
