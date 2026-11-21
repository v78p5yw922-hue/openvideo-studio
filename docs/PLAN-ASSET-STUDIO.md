# 资产工坊 + 主题 + 模型下载｜实施方案（交 Trae 执行）

> 目标：新增一个**独立页面**「资产工坊」，用 H3-Image 生成角色/场景/道具图，并直接入库到现有资产系统；
> 同时给现有 UI 加**浅色/深色主题**，并在页面内支持**本地模型下载**。
>
> 本文件是**执行说明**，按里程碑 M1/M2/M3 拆任务，每项都写了改哪个文件、验收标准是什么。
> 有疑问的地方标了 ❓，不要去猜，先问。

---

## 0. 现状与约束（先读，别改错地方）

| 事实 | 影响 |
|---|---|
| 生成引擎 ComfyUI **0.45.3**，端口 8288，systemd 单元 `comfyui-h3-045.service` | 所有新节点都用这个引擎 |
| 本地只有**视频**模型（fl2va/ref2va 19.5G）+ 视频 VAE + qwen3vl TE | **文生图模型没下**：`minimax_h3_image_pruned_w4a8`（12.5G）+ 图像 VAE（1.2G） |
| 0.45.3 上已有节点：`MiniMaxH3ImageConditioning`（clip, prompt, width, height → CONDITIONING+LATENT） | 本地文生图要用它，配合图像 UNET + 图像 VAE |
| 官方 API 节点 `MinimaxH3ImageNode`（`partner/image/MiniMax`） | 备选路径，需要 key，**本期不做** |
| 渲染队列是**串行**的（一个 GPU） | 资产生成必须排进同一个队列，不能并行抢显存 |
| 现有资产系统：角色图集（`/api/characters/{cid}/refs/*`）、场景母版（`/api/projects/{pid}/scenes/*`） | 资产生成完直接接这两个入口，不另造一套 |
| 前端：React + Vite，源码 `studio/web-src/src`，产物 `studio/web/dist` | 改完要 `npm run build`，再 `deploy-to-wsl.ps1` |

**铁律**：不要新增第二套资产存储；所有生成的图都走现有 `assets/characters/.../refs/` 与 `projects/.../scenes/` 目录。

---

## 1. 里程碑总览

| 里程碑 | 内容 | 是否要 GPU | 预计 |
|---|---|---|---|
| **M1** | 页面骨架 + 分辨率预设 + 提示词助手 + 入库流程 + 主题切换 | 否 | 1 天 |
| **M2** | 模型下载管理器（12.5G + 1.2G）+ 连接测试 | 否（只下载） | 0.5 天 |
| **M3** | 文生图 / 图生图 / 变体 真正跑通（串行队列） | 是 | 1 天 |

**M1 可以先上线**（生成按钮置灰，提示"先下载模型"）。

---

## 2. M1：资产工坊页面

### 2.1 路由与入口

- 顶部导航新增一栏 **「资产工坊」**（Tabs 现在有：剧本 / 审核 / 合成，加一个"资产"）。
- 路由用现有单页状态机（`lib/store.ts` 里加 `tab: 'assets'`），不引入 react-router。
- 页面文件：`studio/web-src/src/components/AssetStudio.tsx` + `AssetStudio.css`。

### 2.2 页面布局（三栏，参考现有中/右栏比例）

```
┌───────────────────────────────────────────────────────────────────────────┐
│ 资产工坊    [文生图] [图生图] [变体]                    主题 ☀/🌙  模型 ●已就绪│
├──────────────────┬──────────────────────────────────┬─────────────────────┤
│ 左栏：生成设置      │ 中栏：候选图（4 宫格）              │ 右栏：提示词助手        │
│                  │                                  │                     │
│ 类型               │  ┌────────┐ ┌────────┐          │ 主体    [输入]        │
│  ○ 角色  ○ 场景    │  │ 候选1  │ │ 候选2  │          │ 状态    [输入]        │
│  ○ 道具  ○ 表情    │  └────────┘ └────────┘          │ 材质    [输入]        │
│                  │  ┌────────┐ ┌────────┐          │ 光      [输入]        │
│ 分辨率             │  │ 候选3  │ │ 候选4  │          │ 镜头    [输入]        │
│  [预设下拉 ▾]      │  └────────┘ └────────┘          │ 风格    [输入]        │
│  2048×2731 5.6MP  │                                  │ 动态    [输入]        │
│  (角色定妆照 3:4)  │  每张下面：seed · 耗时 · [入库▾]  │ ─────────────        │
│                  │                                  │ 词表  [分类 ▾][搜索]  │
│ 张数  [1][2][4]    │                                  │  · monumental…  [+插入]│
│ 步数  [6]          │                                  │  · porcelain…   [+插入]│
│ 种子  [随机] [🎲]  │                                  │  · sword aura…  [+插入]│
│                  │                                  │                     │
│ [生成]（渲染中禁用） │                                  │ [拼成一句话] [复制]    │
│                  │                                  │                     │
│ 队列：1 个任务在跑  │                                  │ 预览：英文终稿         │
└──────────────────┴──────────────────────────────────┴─────────────────────┘
```

### 2.3 左栏：设置项与分辨率预设

**预设必须内置这张表**（`studio/web-src/src/lib/resolutionPresets.ts`）：

| id | 名称 | 宽×高 | 比例 | 像素 | 用途 |
|---|---|---|---|---|---|
| `char_portrait` | 角色定妆照（竖） | 2048×2731 | 3:4 | 5.6MP | 角色三视图/立绘 |
| `char_full` | 角色全身（竖长） | 2048×3584 | 4:7 | 7.3MP | 全身站姿 |
| `scene_wide` | 场景母版（横屏） | 3072×1728 | 16:9 | 5.3MP | 对成片 1344×768 |
| `scene_wide_hd` | 场景母版（2K 横屏） | 2560×1440 | 16:9 | 3.7MP | ❓低于 4MP，仅备选 |
| `mobile_story` | 手机竖屏 | 2160×3840 | 9:16 | 8.3MP | 短视频/封面 |
| `pc_4k` | PC 4K 壁纸 | 3840×2160 | 16:9 | 8.3MP | 壁纸/海报 |
| `poster_2k3` | 竖版海报 | 2560×3840 | 2:3 | 9.8MP | 海报 |
| `avatar` | 头像/表情 | 2048×2048 | 1:1 | 4.2MP | 头像、表情包 |
| `prop_card` | 道具特写 | 2048×2048 | 1:1 | 4.2MP | 道具/食物 |
| `anim_char` | 动物（横） | 2731×2048 | 4:3 | 5.6MP | 猫狗等 |

**UI 上必须写清一句提示**：
> H3-Image 有效范围 **4–20 MP**，低于 4 MP 会糊。成片 1344×768 **不要**直接用来生成。

**其它控件**：

- 张数：1 / 2 / 4（默认 4）
- 步数：默认 6（可调 4–12）
- 种子：随机 / 固定（固定后可"同种子再生成"）
- CFG：固定 1（H3 系无负面分支，不给用户调）

### 2.4 中栏：候选与入库

每张候选卡片：

```
┌──────────────────────┐
│  图片（点击看大图）      │
├──────────────────────┤
│ seed 12345 · 47s      │
│ [设为角色图集] [设为场景母版] [下载] [删除] │
└──────────────────────┘
```

- **设为角色图集**：弹角色选择 → 调 `POST /api/characters/{cid}/refs`（现有上传接口的变体，见 2.6）
- **设为场景母版**：弹场景选择 → `POST /api/projects/{pid}/scenes/{sid}/plate`
- 入库成功后卡片打绿标「已入库 → 妈妈 第 2 张」

### 2.5 右栏：提示词助手（本页最重要的部分）

**结构**：6 个输入框（主体/状态/材质/光/镜头/风格/动态）+ 词表点选 + 实时拼句 + 英文终稿预览。

拼接规则（写在 `lib/promptBuilder.ts`）：

```
终稿 = [风格] + [主体] + [状态] + [材质] + [光] + [镜头] + [动态]，逗号分隔、去重、限长 800 字符
示例：flat cel-shaded color, clean line art, an 8-year-old village boy,
      standing still, washed-out blue-and-white striped vest, warm rim light from the left,
      medium shot, eye level, 50mm
```

**词表数据**：`studio/web-src/src/data/prompt-glossary.json`（本方案已附初始版，约 140 条 16 类）
交互：分类下拉 + 搜索框 + 每行右侧 `[+插入]`（插入到当前聚焦的输入框）。

**"拼成一句话"** 按钮 → 右侧预览框显示英文终稿，可复制。

### 2.6 后端接口（M1 只做存储与入库，生成接口先返回 501）

```
GET  /api/assets/presets                → 分辨率预设（也可前端硬编码，二选一，建议后端给）
POST /api/assets/generate               → {kind, prompt, width, height, count, steps, seed, source_image?}
                                           M1: 若模型未就绪返回 409 {error:"model_missing", download:"/api/models/status"}
POST /api/assets/candidates/{id}/adopt  → {target: "character"|"scene", cid?/pid+sid, index?}
GET  /assets/generated/{file}           → 生成的图（静态目录 studio-data/generated/）
DELETE /api/assets/candidates/{id}
```

**数据落盘**：

```
studio-data/generated/{yyyyMMdd}/{candidate_id}.png      原图
studio-data/generated/{yyyyMMdd}/{candidate_id}.json     元数据（prompt/seed/steps/size/耗时）
```

**数据库**（`studio/core/schema.sql` 新表）：

```sql
CREATE TABLE IF NOT EXISTS asset_candidates (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- character|scene|prop|emotion
    prompt      TEXT NOT NULL,
    negative    TEXT NOT NULL DEFAULT '',
    width       INTEGER NOT NULL,
    height      INTEGER NOT NULL,
    seed        INTEGER NOT NULL,
    steps       INTEGER NOT NULL,
    path        TEXT NOT NULL,
    elapsed_s   REAL NOT NULL DEFAULT 0,
    source_path TEXT NOT NULL DEFAULT '',  -- 图生图的原图
    adopted_to  TEXT NOT NULL DEFAULT ''   -- 例：character:xiaoshitou:2
);
```

### 2.7 主题支持（浅色/深色）

**现状问题**：`ShotCard.css`、`TakesModal.css`、`AudioPanel.css` 里写死了 `rgba(255,255,255,.08)` 这类值，浅色主题下会看不见。

步骤：

1. 在 `studio/web-src/src/styles/tokens.css` 里定义两套：

```css
:root[data-theme="dark"] {
  --bg: #14161a; --panel: #1c1f24; --panel-2: #23272e;
  --text: #e6e8ec; --text-dim: #9aa0a8;
  --line: rgba(255,255,255,.10);
  --accent: #5aa0ff; --warn: #c96a2b; --ok: #4fae72; --danger: #d05b5b;
}
:root[data-theme="light"] {
  --bg: #f5f6f8; --panel: #ffffff; --panel-2: #eef1f5;
  --text: #1b1f24; --text-dim: #5f6b76;
  --line: rgba(0,0,0,.10);
  --accent: #2f6fe4; --warn: #b4571f; --ok: #2f8a55; --danger: #c04545;
}
```

2. 全量替换硬编码：`rgba(255,255,255,x)` → `var(--line)` / `var(--panel-2)`；`#000` 背景 → `var(--bg)`。
   **验收**：在浅色主题下逐页截图，不能出现"白字白底"或"黑块"。

3. 切换入口：顶部栏右侧 `☀/🌙` 按钮；写 `localStorage.theme`；默认跟随系统 `prefers-color-scheme`。

4. 首屏防闪：`index.html` 里内联一段脚本，在 React 挂载前设置 `document.documentElement.dataset.theme`。

---

## 3. M2：模型下载管理器

### 3.1 需要的文件

| 文件 | 目标路径（ComfyUI-up） | 大小 |
|---|---|---|
| `minimax_h3_image_pruned_w4a8.safetensors` | `models/diffusion_models/` | 12.5 GB |
| `minimax_h3_image_vae.safetensors` ❓（确切文件名待确认） | `models/vae/` | 1.2 GB |

❓**下载源**：本方案不写死 URL。在 `studio/core/model_sources.json` 里配置：

```json
{
  "h3_image_unet": {
    "file": "minimax_h3_image_pruned_w4a8.safetensors",
    "subdir": "diffusion_models",
    "bytes": 13421772800,
    "url": "",
    "sha256": "",
    "note": "填 HuggingFace 下载直链；sha256 可留空"
  },
  "h3_image_vae": { "...": "同上" }
}
```

> 执行要求：**url 为空时页面显示"未配置下载源"**，不要静默失败。

### 3.2 接口

```
GET  /api/models/status        → [{key, file, exists, bytes, bytes_on_disk, state, pct, speed_mbps, eta_s, error}]
POST /api/models/download      → {key}  启动后台下载（断点续传）
POST /api/models/cancel        → {key}
POST /api/models/verify        → {key}  校验大小/哈希
```

### 3.3 下载实现要点

- 用 `httpx`/`requests` 流式下载，`Range` 续传，写 `.part` 后 `rename`
- 进度写内存字典（同 `BED_JOBS` 的写法），页面 2 秒轮询
- 磁盘空间预检：所需空间 ×1.2，不够直接拒绝
- 下载完成后**不自动重启引擎**；页面提示「重启生成引擎后生效」，给一个按钮调 `systemctl restart comfyui-h3-045.service`（需确认弹窗）

### 3.4 页面位置

资产工坊右上角一个「模型状态」小面板：`● 已就绪 / ◐ 未下载 12.5 GB [下载] / ✕ 下载失败（原因）`。

---

## 4. M3：真正生成（要 GPU）

### 4.1 工作流（本地 H3-Image 文生图）

```
UNETLoader(minimax_h3_image_pruned_w4a8)
  + CLIPLoader(qwen3vl_32b_minimax_h3_int8_convrot)
  + VAELoader(minimax_h3_image_vae)
→ MiniMaxH3ImageConditioning(clip, prompt, width, height)   → CONDITIONING, LATENT
→ KSamplerSelect(euler) + BasicScheduler(steps, denoise=1.0)
→ RandomNoise(seed) + BasicGuider(cond) + SamplerCustomAdvanced
→ VAEDecode → SaveImage
```

❓**必须先跑通一张**再写批量：确认节点组合、显存是否够（4MP 可能爆 24G）、单张耗时。
若显存不够，回落方案：先出 2048×2048（4.2MP）再放大。

### 4.2 图生图

`MiniMaxH3ImageConditioning` 带可选 `images` 输入（参考图）→ 传入原图 + 描述"保持这个人，改成侧面"。
参数：`denoise` 0.45–0.7（UI 给滑块，默认 0.55）。

### 4.3 变体批量

同一角色一次生成：正面/侧面/背面/表情 4 组 × 2 张 → 8 张候选，落同一 candidate 批次（`batch_id` 字段 ❓是否加）。

### 4.4 队列接入

新增 job kind = `asset`：
- `POST /api/assets/generate` → 入队（复用 `service.enqueue_job`）
- runner 里加 `elif job["kind"] == "asset": _run_asset(...)`
- **与渲染互斥**：同一条队列即可；若正在渲染，资产生成排队等待（页面显示"前面还有 N 个渲染任务"）

---

## 5. 词表（prompt-glossary.json）规范

### 5.1 数据结构

```json
{
  "version": 1,
  "categories": [
    {
      "id": "person",
      "label": "人物外观",
      "items": [
        {"zh": "高大雄伟的建筑", "en": "a monumental stone hall, towering, imposing scale",
         "example": "a monumental stone hall, towering, imposing scale, vertiginous low-angle shot",
         "tags": ["architecture", "scale"]}
      ]
    }
  ]
}
```

### 5.2 必含的 16 类（初始版已给 140+ 条）

1. 人物外观（体型/脸型/皮肤/发型/眼睛/年龄）
2. 服装与材质（棉布/丝绸/铠甲/麻布/雨衣/补丁）
3. 建筑与场景（雄伟/破败/乡村/城市/室内/室外/庭院）
4. 自然与天气（雨/雪/雾/风/晴/黄昏）
5. 光线（主光/轮廓光/逆光/硬光/柔光/窗光/体积光）
6. 镜头与构图（景别/角度/焦段/景深/构图法）
7. 画风（2D 赛璐璐/水彩/国风/厚涂/黑白线稿/像素/复古胶片/3D PBR）
8. 动作与动态（中段动作/运动模糊/冻结/慢动作感）
9. 特效（剑气/龙形/火焰/水/烟/粒子/能量场）
10. 状态变化（消失/溶解/透明/破碎/生长/燃烧）
11. 情绪与表情（微表情/眼神/嘴角/呼吸）
12. 动物（犬/猫/鸡/牛/鸟）
13. 道具与武器（食物/篮子/锅/火柴/农具）
14. 时代与地域（1996 中国北方农村/县城/集市）
15. 画面质感（颗粒/暗角/色差/景深/漏光）
16. 否定替代写法（想要"没有"时怎么写）

### 5.3 三条使用原则（页面里也要提示）

```
① 一个概念 ≤3 个形容词（多了互相抵消）
② 抽象词换成可见证据："他很威严" → "shoulders squared, chin level, gaze fixed forward"
③ 不要写否定（H3 无负面分支）：不写"没有胡须"，写"clean-shaven"
```

---

## 6. 验收标准（Trae 交活前自查）

### M1

- [ ] 顶部有「资产工坊」入口，切页不刷新、不丢状态
- [ ] 左栏 10 个分辨率预设可选，显示 MP 与用途；选 1344×768 时出黄条警告
- [ ] 右栏 6 个输入框 + 词表分类/搜索/插入可用；「拼成一句话」输出英文终稿
- [ ] 中栏 4 宫格占位（无模型时显示"先下载模型"）
- [ ] 浅色/深色切换：全部 5 个页面无白字白底/黑块；刷新后保持；跟随系统
- [ ] 入库按钮在无候选时禁用
- [ ] `npm run build` 通过、`pytest studio/tests` 274 全绿

### M2

- [ ] 模型面板显示三个文件状态；未配置 URL 时明确提示
- [ ] 下载显示速度/剩余时间；中断后能续传
- [ ] 磁盘不足时拒绝并说明

### M3

- [ ] 一张 2048×2048 图能出，页面显示耗时与显存峰值
- [ ] 渲染任务进行中提交资产生成 → 排队，不抢显存
- [ ] 入库后角色图集/场景母版里立刻可见、可排序、可设"已验证"

---

## 7. 改动文件清单（给 Trae 的落点）

**前端（新增）**

```
studio/web-src/src/components/AssetStudio.tsx
studio/web-src/src/components/AssetStudio.css
studio/web-src/src/components/PromptAssistant.tsx
studio/web-src/src/components/ModelStatusPanel.tsx
studio/web-src/src/lib/promptBuilder.ts
studio/web-src/src/lib/resolutionPresets.ts
studio/web-src/src/data/prompt-glossary.json     ← 本方案附带
studio/web-src/src/lib/theme.ts
```

**前端（修改）**

```
studio/web-src/src/App.tsx              加 tab
studio/web-src/src/lib/store.ts         tab 状态 + assets 相关方法
studio/web-src/src/lib/api.ts           assets/models 接口
studio/web-src/src/styles/tokens.css    两套主题变量
studio/web-src/src/components/*.css     替换硬编码颜色
studio/web-src/index.html               首屏主题脚本
```

**后端（新增）**

```
studio/core/assets.py                   候选图 CRUD + 入库
studio/core/models_dl.py                下载管理器
studio/core/model_sources.json          下载源配置
studio/api/app.py                       新路由（约 8 个）
studio/exec/runner.py                   加 kind=asset 分支
studio/core/schema.sql                  asset_candidates 表
```

---

## 8. 风险与回滚

| 风险 | 应对 |
|---|---|
| 4MP 图爆显存（24G） | 先文档里跑一张验证；爆了就回落 2048×2048 + 放大 |
| 下载源失效/误导 | `model_sources.json` 可改；页面显示"未配置"而不是静默 |
| 资产生成抢渲染 GPU | 同一串行队列 + 页面显示"前面还有 N 个任务" |
| 主题改造引入样式回归 | 逐个组件替换后截图对比；保留 `data-theme="dark"` 为默认 |
| 新增页面拖慢首屏 | 资产页用 `React.lazy` 懒加载 |

---

## 9. 交付顺序（建议 Trae 按这个顺序提交 PR）

1. `theme`（独立、低风险，可先合）
2. `resolutionPresets` + `promptBuilder` + `prompt-glossary.json`（纯数据/纯函数，可单测）
3. `AssetStudio` 页面骨架 + 路由（无生成）
4. 后端 `asset_candidates` + 入库接口
5. 模型下载管理器
6. 生成工作流（要 GPU，最后做）

---

## 10. GPU 二选一：视频与图像不能同时跑（交互与实现）

### 10.1 事实约束

```
RTX 4090 · 24 GB（实测 total 24564 MiB）
视频：UNET 19.5G + TE 25.3G（分片 staged）→ 跑起来常驻 16–23.5 GB
图像：UNET 19.5G + TE 25.3G（共用）      → 4MP 预计 18–21 GB
两者不可能同时驻留；切换一次要把 UNET 和 TE 各换一遍（≈1–2 分钟）
```

所以不是"要不要禁止"，而是**排队 + 让用户看见代价**。

### 10.2 三种模式（默认 B）

| 模式 | 行为 | 适用 |
|---|---|---|
| **A. 硬禁止** | GPU 忙时按钮禁用，提示"等渲染结束再来" | ❌ 不推荐，用户要反复试 |
| **B. 软排队（默认）** | 按钮变「排队生成（预计等 N 分钟）」，进队后自动开跑 | ✅ 白天混用 |
| **C. 渲染优先** | 渲染批次跑完才处理资产任务；资产永不通宵占 GPU | ✅ 夜间批量渲 |

配置：`projects.settings.gpu_policy = "queue" | "render_priority"`（默认 `queue`）。

### 10.3 界面要求

**顶部常驻状态条（全局，不只是资产页）**

```
● GPU 空闲
◐ 渲染中 · s08 · 25% · 约 12 分钟
◑ 资产生成中 · 2048×2048 · 第 2/4 张
✕ GPU 异常（显存不足/引擎无响应）
```

**生成按钮的三种形态**

```
空闲          → [生成]（可点）
忙            → [排队生成 · 预计等 12 分钟]（可点，点了进队）
显存确实不够  → [生成]（禁用）+ 一行原因："当前空闲 3.2 GB，4MP 需要约 18 GB"
```

**任务中心**：列出排队/进行中的任务，可取消、可看进度。
**文案必须写清原因**：`24 GB 显存只能装一套模型，视频和图不能并行` —— 否则用户以为是 bug。

### 10.4 接口

```
GET  /api/gpu/status
  → { total_mib: 24564, used_mib: 21000, free_mib: 3564, util: 99,
      current: {kind: "render", shot_id: "s08", pct: 25, eta_s: 720} | null }

POST /api/assets/generate
  → { job_id, queue_position, eta_s, accepted: true }
  显存不足且无法排队 → 409 { error: "vram_insufficient", need_mib, free_mib }
```

实现：`free_mib` 用 `/mnt/c/Windows/System32/nvidia-smi.exe --query-gpu=memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader`（**WSL 已实测可调用**）。

### 10.5 执行器规则

1. 全局**一条**队列（视频渲染 + 资产生成共用），永远串行。
2. 同类任务相邻执行（批次化），减少模型切换：
   ```
   先进先出，但允许"同类合并"：已在跑 render 时，先把同一批 render 跑完再处理 asset
   ```
3. 每次**类型切换**前：调用 ComfyUI `/free`（卸载模型）→ 等 2 秒 → 提交。
4. 提交前预检：
   ```
   need = kind == "video" ? 22000 : (width*height/1e6 * 4.5 + 4000)   # 经验公式，实测后校准
   if free_mib < need + 2000: 等待，不提交（每 30 秒重查一次）
   ```
5. 失败兜底：OOM 时任务标记 `failed(vram)`，不重试（重试多半还是 OOM），提示降分辨率。

### 10.6 验收

- [ ] 渲染中提交资产任务 → 进队且显示预计等待，不报错
- [ ] 渲染完成后资产任务自动开始，无需刷新
- [ ] 状态条实时反映"渲染中/生成中/空闲"，切换类型时显示"切换模型中…"
- [ ] 人为把显存占到 22 GB 再提交 4MP 图 → 返回 409，界面显示"空闲 3.2 GB，需要约 18 GB"
