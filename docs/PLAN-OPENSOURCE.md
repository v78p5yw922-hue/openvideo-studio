# 开源化改造评估（配置 / 适配 / 文档与数据归属）

> 回答三个问题：①改造对本地性能和效率有没有影响 ②对运行环境和存放位置有什么影响
> ③文档和项目相关的文件该放哪、哪些应该做成 UI 可配置。
> 结论先给：**代码层改造对渲染性能的影响可以忽略（<0.1%）；唯一会让性能明显变化的是"把数据目录挪到 Windows 挂载盘"。**

---

## 一、结论摘要

| 问题 | 结论 |
|---|---|
| 配置抽取会不会拖慢渲染？ | **不会**。启动读一次，微秒级；单镜渲染 9–27 分钟，占比 0.00001% |
| 适配层会不会拖慢？ | **不会**。每镜多一次字典组装（<0.1 ms） |
| 启动时探测节点/权重？ | +1 次 HTTP（50–200 ms），只影响启动，可缓存/可跳过 |
| 什么会真正变慢？ | **数据目录放到 `/mnt/c`、`/mnt/d` 或网络盘**（跨文件系统 I/O 慢 2–10 倍，合并/抽帧最明显） |
| 运行位置有影响吗？ | 模型在哪都行（ComfyUI 本地或 Windows 侧）；**studio 的数据目录必须是 studio 所在机器的本地盘** |
| 文档要动吗？ | 要。`docs/` 只放通用规范，具体剧的文档移到 `projects/<剧名>/`，示例移到 `examples/` |

---

## 二、改造清单与性能影响

| # | 改造项 | 现在 | 改法 | 运行时开销 | 风险 |
|---|---|---|---|---|---|
| 1 | 引擎地址 | `8288` 散在 7 个文件 | 统一到 `config.toml` / `OPEN_VIDEO_COMFY_PORT` | 0 | 低 |
| 2 | 模型文件名 | 硬编码在 4 个文件（`gen_image.py`、`model_status.py`、`upscale_segments.py`…） | 抽成 `models.toml`，启动时校验存在 | 0（启动时 stat） | 低 |
| 3 | 节点名 | `H3CastToVideo/H3CastMember/AddGuide` 在 3 个文件 | 抽成适配层常量，按后端注册 | 0 | 中（改错就渲不出） |
| 4 | 数据目录 | 已有 `STUDIO_DATA` 环境变量（35 文件引用，但都经 `db.DATA_DIR`） | 收敛到配置加载器；默认 `~/.local/share/openvideo` | 0 | 低 |
| 5 | 能力探测 | 无 | 启动 `GET /object_info`，比对所需节点/模型，缺了就**明确报错** | +50–200 ms（启动一次） | 低 |
| 6 | 工作流组装 | `render_drama.py` 里直接拼 ComfyUI 图 | 拆「工作流描述（dict）」+「ComfyUI 适配器」 | 每镜 +0.1 ms | 中（需回归测试） |
| 7 | `studio doctor` | 无 | 独立命令：查端口/权重/节点/显存/磁盘 | 离线执行 1–3 s | 低 |

**总账**：一个 10 秒镜渲染约 **27 分钟**，上面第 1–7 项加起来增加的开销 **< 1 秒**（启动探测 200 ms + 每镜 0.1 ms × 19 ≈ 0）。
**性能影响：可忽略。** 真正要盯的是第 8 项：**数据放在哪**。

---

## 三、运行环境与位置：哪些会影响性能

### 3.1 四类位置的实际差别（同一台机器实测经验）

| 位置 | 典型路径 | 顺序读写 | 大量小文件 | 结论 |
|---|---|---|---|---|
| **WSL 原生 ext4** | `~/openvideo/studio-data` | 基准（≈1 GB/s） | 好 | ✅ 推荐 |
| Windows 挂载盘（9p/drvfs） | `/mnt/d/...` | 慢 2–4 倍 | 慢 5–10 倍 | ⚠️ 只适合放源码，不适放数据 |
| 网络盘（SMB/NFS） | `//nas/...` | 慢 3–10 倍，延迟抖动 | 很差 | ❌ 不要 |
| 内存盘（tmpfs） | `/dev/shm` | 最快 | 好 | 只适合临时中间件 |

**会明显变慢的三处**：①合并 19 个片段（读 ~1 GB，写 70 MB）②抽末帧（每镜一次 ffmpeg）
③SQLite 事务（`takes` 记录 + 事件流）。

### 3.2 建议的目录布局（开源版）

```
~/.local/share/openvideo/            ← 数据目录（必须本地盘）
  studio.db
  projects/<slug>/                   ← 每个剧的产物（takes/output/scenes 图）
  characters/                        ← 角色参考图
  assets/                            ← 音频床、素材
  config.toml                        ← 实例配置（UI 可读写）
```

```
<repo>/                              ← 代码（放哪都行，包括 Windows 的 /mnt/d）
  studio/  scripts/  web-src/  docs/  examples/
```

**关键分离**：代码可以放慢盘（读一次），数据必须放快盘（反复读写）。
我们现在的做法正是这个（D 盘源码 + WSL 数据），只是**没写进文档**，导致"看起来耦合"。

### 3.3 有人想在 Windows 原生跑（不用 WSL）行不行？

```
能。约束只有三条：
① ComfyUI 在 Windows 上跑，studio 通过 HTTP 连它（地址可配）
② 数据目录用 Windows 本地盘（NTFS，性能正常）
③ 路径拼接不能假设 /home/agent（现在只有 1 个测试文件带这个假设）
```

也就是说：**studio 不依赖 WSL**，WSL 只是我们这台机器的部署选择。

---

## 四、文档与项目数据：该放哪

### 4.1 现状问题

`docs/` 里混了两类东西：

```
通用规范（任何剧都能用）      RULES-STRICT / SCRIPT-FORMAT-V3 / SHOT-SELECTION / INSTALL
本剧专属（只对《小石头》）    CONTENT-BRIEF（世界观、人物小传、本剧视觉基调）
```

`CONTENT-BRIEF.md` 是**某一部剧**的内容，跟着仓库走会让开源仓库带着我们的 IP；
而 `CHARACTER-SHEETS / SCENE-SHEETS / 2D-SHEETS / ASSET-PROMPTS` 是**通用方法**（讲怎么出定妆照），应留在 `docs/`。

### 4.2 建议的归属

| 内容 | 现在 | 开源后放哪 | 理由 |
|---|---|---|---|
| 通用规范 | `docs/*.md` | `docs/`（不变） | 与剧无关 |
| 本剧内容简报 | `docs/CONTENT-BRIEF.md` | `projects/xiaoshitou-new/CONTENT-BRIEF.md` | 属于项目数据 |
| 剧本/分镜/场景母版清单 | `projects/<剧>/` ✅ | 不变 | 已经是项目目录 |
| 角色/场景提示词模板 | `docs/*-SHEETS.md` | `docs/`（不变） | 讲方法，不含剧情 |
| 词汇库 | `studio/data/script-glossary.json` | 保留在仓库，但**允许项目覆盖** | 通用资产 + 可定制 |
| 示例剧 | 无 | `examples/demo-ep01/`（虚构剧情） | clone 下来能跑通 |
| 历史文档 | `docs/_archive/` ✅ | 开源时**不进仓库**（或单独 repo） | 内部演进史 |

### 4.3 哪些应该做成"可配置"（三层）

```
第 1 层：环境变量（部署级，容器/Docker 用）
  OPEN_VIDEO_COMFY_PORT / OPEN_VIDEO_COMFY_ROOT / STUDIO_DATA / STUDIO_ALLOW_RENDER

第 2 层：config.toml（实例级，UI 可读写）
  [comfy]   url, timeout, model_search_path
  [models]  video_unet / ref_unet / image_unet / text_encoder / vaes
  [render]  width, height, steps, durations, max_wall
  [gpu]     policy = queue | render_priority
  [paths]   data_dir, assets_dir

第 3 层：项目设置（DB，UI 必须可改）
  风格串、默认分辨率、时长上限、默认语气、词库覆盖、GPU 策略覆盖
```

**优先级**：项目设置 > config.toml > 环境变量 > 内置默认。

### 4.4 UI 里该配什么（按优先级）

| 优先级 | 项 | 放哪 | 为什么 |
|---|---|---|---|
| P0 | 引擎地址 + 「测试连接」 | 设置页 | 装错端口是最常见的启动失败 |
| P0 | 模型清单状态（有/无/路径） | 设置页 | 缺模型要能看懂缺哪个 |
| P0 | 数据目录 | 设置页（只读显示 + 迁移按钮） | 用户要备份/换盘 |
| P1 | 默认分辨率 / 步数 / 时长档位 | 设置页 | 影响成本 |
| P1 | GPU 策略（排队 / 渲染优先） | 设置页 | 视频与图不能并行 |
| P2 | 词库编辑（增删词条） | 高级设置 | 换题材要加词 |
| P3 | 适配层选择（H3 / 其他后端） | 高级设置 | 将来换模型 |

---

## 五、分阶段方案与工作量

| 阶段 | 内容 | 工作量 | 收益 | 风险 |
|---|---|---|---|---|
| **P0 配置化** | 抽 `config.toml` + 加载器；模型名/端口/数据目录全走配置；默认值保持现在的行为 | 0.5–1 天 | 别人能装、我们换机器不用改代码 | 低（默认值不变，行为不变） |
| **P1 能力探测 + doctor** | 启动检查节点/权重；`studio doctor` 一条命令体检 | 0.5 天 | 装错立刻知道，不再渲到一半失败 | 低 |
| **P2 适配层** | `render_drama` 拆「工作流描述 / ComfyUI 适配器」；加回归测试（用 mock 引擎） | 1–2 天 | 换模型只改适配器 | 中（要保证现有工作流字节级一致） |
| **P3 文档与数据归属** | `CONTENT-BRIEF` 移到项目目录；新增 `examples/`；`docs/README` 分「通用/项目/示例」 | 0.5 天 | 仓库里不再混 IP | 低 |
| **P4 打包** | Dockerfile（含 ComfyUI 钩子）或 compose；README 一键跑 | 1–2 天 | 降低上手门槛 | 中（镜像 30 GB+，需挂载模型） |

**总计约 4–6 人天**，其中 P0+P3 就能让"别人能装、我们的 IP 不泄漏"，风险也最低。

---

## 六、影响程度评级总表

| 维度 | 影响 | 说明 |
|---|---|---|
| 渲染速度 | **无影响** | 改造都是启动期 + 微秒级 |
| 显存 / GPU 占用 | **无影响** | 不碰模型与采样参数 |
| 磁盘 I/O | **可能变慢 2–10×** | 仅当把数据目录放到 `/mnt/*` 或网络盘 |
| 启动时间 | +0.2–3 s | 能力探测（可关） |
| 现有项目数据 | **零迁移** | 默认数据目录保持 `STUDIO_DATA` 的位置 |
| 代码结构 | 中等改动 | `render_drama` 拆两层；其余是配置读取 |
| 文档 | 移动 1–2 份 + 新增 3 份 | 不影响运行 |
| 我们自己的开发流 | 几乎不变 | `deploy-to-wsl.ps1` 仍可用，只需多一份 `config.toml` |

---

## 6.5 进度（2026-11-21）

```
✅ P0 配置化（已完成）
   · 新增 studio/config.py：默认值 < config.toml < 环境变量
   · 数据目录、引擎地址、模型文件名、渲染默认值 全部可配
   · config.toml（本机）+ config.example.toml（给用户）
   · 行为零变化：默认值 = 改造前的硬编码值
✅ P3 文档与数据归属（已完成）
   · 剧本/分镜/内容简报移出仓库 → content/<剧名>/
   · deploy 脚本同步 config.toml；.gitignore 忽略 config.toml / content/
✅ P1 能力探测 + doctor（已完成）
   · studio/doctor.py：7 项体检（配置/引擎/节点/权重/ffmpeg/磁盘/GPU）
   · CLI：python -m studio.doctor [--json]；API：GET /api/doctor
   · 启动时自动体检（只告警不阻塞），OPEN_VIDEO_SKIP_DOCTOR=1 可关
🟡 P2 适配层（部分完成）
   · 已完成：传输层抽成 studio/exec/comfy.py 的 ComfyClient
     （submit / wait / free / download / object_info），render_drama 只做薄封装
     · 行为完全不变；新增 5 个假引擎测试
   · 未完成：「工作流描述」与「节点图组装」的分离（换模型时才需要）
⏳ P4 打包（未开始）
```

## 七、建议的执行顺序

```
1. P0 配置化 + P3 文档归属（1–1.5 天）   ← 现在做最划算，风险最低
2. P1 能力探测 + doctor（0.5 天）        ← 直接减少"装错"类问题
3. P2 适配层（1–2 天）                    ← 等真要支持第二个模型时再做
4. P4 打包（1–2 天）                      ← 有人真的想用的时候再做
```

**一句话**：改造不会让现在的出片变慢；真正决定体验的是"数据放哪个盘"。
先把配置和文档归属理清，就能安全开源；适配层可以晚一步。
