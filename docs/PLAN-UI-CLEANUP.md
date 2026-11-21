# UI 清理与语义收敛（追加给 Trae 的改进项）

> 背景：Trae 正在重构整个 UI 布局并加登录/用户操作。这份文件列出**重构时顺手要做的语义清理**，
> 避免新版面把旧的重复按钮和隐形字段照搬过去。

---

## 一、工具栏四个按钮：留一个、删一个、降级一个、合并两个

| 现在的按钮 | 实际行为（已核对代码） | 处置 |
|---|---|---|
| **自动转场** | 逐镜判断：上一镜与本镜**角色集合相同 → chain**，否则 cut（`service.auto_transitions`） | **保留**，改名「按角色智能」 |
| **全部 chain** | 第 2 镜起全部置 `transition=chain`，并清空 anchor | **删除**，并入批量菜单 |
| **全部接上一帧** | 第 2 镜起全部置 `chain_ref=true` | **降级为逐镜开关**（见下） |
| **生成计划** | 打 plan → 确认 → 入队（唯一真正执行的入口） | **保留并提到最显眼位置** |

### 目标形态

```
工具栏：[批量设置 ▾]  [生成计划（主按钮）]
         ├─ 按角色智能（默认）
         ├─ 全部硬切
         └─ 全部接上一帧（仅空镜生效；人物镜会提示"不生效"）

镜头卡：[接续 ▾] 无 / 接上一镜末帧 / 姿势承接 / 动作中段
```

### 为什么「全部接上一帧」必须降级

实测（第 1 集 s02）：把上一镜末帧钉在 frame 0 之后，模型把这一镜当成**上一镜的延续**，
剧本写的"撑手肘坐起来喊妈"完全没有发生，他全程趴着。

```
接续只在"这一镜本来就是上一镜动作的延续"时才成立
做成全局按钮 = 逼迫所有镜头变成延续镜头 = 毁掉整集节奏
```

**逐镜开关要带前置检查**（满足才推荐，不满足只黄条提示）：

- 上一镜末帧存在且可用？
- 机位高度差 ≤ 一档？
- 本镜首拍是「延续动作」而不是「新动作」？

---

## 二、字段收敛：`transition` + `chain_ref` → 一个 `continuation`

现状是两个字段表达同一件事，用户在界面上无法分辨：

```
transition = "chain"     ← 只对空镜真的接上一帧
chain_ref  = true        ← 对人物镜叠加"末帧当参考图 / AddGuide 钉帧"
```

建议合并：

```ts
type Continuation = 'cut' | 'frame_pin' | 'state_match' | 'action_match'
// cut         硬切（默认）
// frame_pin   接上一镜末帧（原 chain + chain_ref，仅空镜/连续动作镜允许）
// state_match 只写姿势承接，不钉帧（靠文本）
// action_match 动作中段接（靠切点位置）
```

后端迁移：`transition=chain && chain_ref` → `frame_pin`；`transition=chain && !chain_ref` → `cut` + 空镜特例。

---

## 三、本剧覆盖描述：**保留**，但有三个必须修的问题

### 它是什么

```
project_cast.description_override   本项目内覆盖角色外观描述
project_cast.refs_override_json     本项目内覆盖角色参考图
```

用途：**同一角色在本集换了造型**（受伤/换衣/年龄跳跃/换季），不动全局角色卡。

渲染时 `service.py` 会把覆盖合进角色的 description，再进提示词。

### 为什么保留

这一集"妈妈围着围裙、袖口是湿的"，下一集"妈妈穿新衬衫去镇上"——不改全局卡，只改本剧。
如果没有它，只能改全局卡，会污染其他项目。

### 三个必须修的问题

| 问题 | 现状 | 修法 |
|---|---|---|
| **不回显** | i18n 明写「已有覆盖内容不会回显（接口不返回）」 | `GET /api/projects/{pid}/characters` **必须返回 override 原文**，界面上显示"当前覆盖：……" |
| **隐形生效** | 用户不知道哪些角色有覆盖 | 角色卡加徽标「本剧覆盖」，左侧列表也标（已有 `left.overridden` 文案，未接线就接上） |
| **无法对比/回退** | 只有"保存覆盖"/"留空=清除" | 加「查看全局描述 / 恢复全局」两个动作 + diff 视图（全局 vs 本剧） |

### 与分镜的分工（写进文档，避免以后再混）

```
本剧覆盖 = 整集造型（贯穿全集的变化）
分镜服装状态 = 单镜状态（湿了/脏了/披麻袋/受伤流血）
两者同时存在时，分镜优先（更具体）
```

---

## 四、验收标准（Trae 自查）

- [ ] 工具栏只剩「批量设置」「生成计划」，批量菜单三项，动作与旧版一致
- [ ] 镜头卡有「接续」下拉，选"接上一镜末帧"时若不满足前置条件，出现黄条提示（不阻止）
- [ ] 角色覆盖：接口返回原文；界面能看、能对比、能一键恢复全局
- [ ] 有覆盖的角色在角色卡与左侧列表都有「本剧覆盖」标记
- [ ] 旧数据迁移后，`transition` / `chain_ref` 不再出现在任何界面上

---

## 五、改动落点

```
前端
  components/ShotGrid.tsx        工具栏：三按钮 → 批量菜单
  components/ShotCard.tsx        加「接续」下拉
  components/AssetManagerModal.tsx  覆盖描述：回显 + 对比 + 恢复
  lib/store.ts                   chainAll / chainRefAll 合并为 setContinuation
  lib/api.ts                     新增 getProjectCharacters（含 override 原文）

后端
  studio/core/service.py         list_characters 带 override；set_project_character_override 返回原文
  studio/api/app.py              补 GET 返回字段
  studio/core/schema.sql         （可选）continuation 字段迁移
```

---

## 六、M2 补充：显存守卫（与本文件同批实现）

```
GET  /api/gpu/status    → {total, used, free, util}    ← 用 nvidia-smi.exe（WSL 已可调用）
QUEUE 策略：GPU_POLICY=serial  一个类型跑完再跑另一个；切换时调 /free
提交前预检：空闲显存 < 需求 → 不入队，页面显示"GPU 忙：渲染中 s08 25%，资产任务排队中"
```
