# 文档索引

> 只保留**现在还在用**的文档。过期的都在 `_archive/`，别删，出问题时要查历史。

## 现在要读的（按角色）

### 写剧本 / 写分镜

| 文档 | 作用 |
|---|---|
| `RULES-STRICT.md` | **60 条写作规则**（台词人话、节拍密度、状态/前置/去向、门与步态） |
| `SCRIPT-FORMAT-V3.md` | **当前格式**：镜头表 10 列 + 七槽 + 接续/服装状态/道具卡 |
| `SCRIPT-FORMAT.md` | v1 格式基线（代码注释仍引用这个路径，保留） |
| `SHOT-SELECTION.md` | 怎么选机位、景别、运镜（避免连续固定） |
| `REVIEW-LESSONS.md` | **12 条真实打回**，写之前先扫一遍 |
| `studio/data/script-glossary.json` | 词汇库（7 类 150+ 条），写的时候从里面挑词 |

### 做素材（角色 / 场景 / 图）

| 文档 | 作用 |
|---|---|
| `CHARACTER-SHEETS.md` | 角色定妆照怎么写（含一致性锁、服装锁、步态锁） |
| `SCENE-SHEETS.md` | 场景母版怎么写（机位 A/B、固定物件、光源） |
| `2D-SHEETS.md` | 2D 赛璐璐风格的三视图模板 |
| `ASSET-PROMPTS.md` | 资产提示词拼装规则 |
| `CONTENT-BRIEF.md` | 本剧的世界观、人物小传、视觉基调 |

### 部署 / 运维

| 文档 | 作用 |
|---|---|
| `INSTALL.md` | 从零装（ComfyUI 版本、节点包、权重清单） |
| `DEPLOY.md` | 日常部署（`deploy-to-wsl.ps1`，同步 + 重启） |
| `MODELS.md` | 三个 UNET / 三个 VAE / 文本编码器，大小与用途 |
| `KNOWN-LIMITS.md` | 已知限制（单卡、竖屏、审稿边界） |

### 进行中的方案

| 文档 | 状态 |
|---|---|
| `PLAN-ASSET-STUDIO.md` | 资产工坊（M1 页面 / M2 下载 / M3 生成）+ GPU 二选一策略，交 Trae |
| `PLAN-UI-CLEANUP.md` | 按钮去重（自动转场/全部 chain/接续）+ 本剧覆盖三处修复 |
| `PLAN-OPENSOURCE.md` | 开源化改造：配置/适配的性能影响、运行位置、文档与数据归属、P0–P4 方案 |

### 总览

| 文档 | 作用 |
|---|---|
| `PROJECT-SUMMARY.md` | **技术 + 产品总览**（架构、约束、契约、功能、独立度、开源评估、面试要点） |

> 剧本、分镜、内容简报**不在 docs/**：它们在 `content/<剧名>/`（可用 `config.toml` 的
> `[paths] content_dir` 或环境变量 `OPEN_VIDEO_CONTENT_DIR` 改位置）。

---

## `_archive/`（历史，不要删）

```
_archive/2026-11-superseded/     被新版取代的单份文档
  ARCHITECTURE.md                旧架构（40 路由时代，已被 PROJECT-SUMMARY §2 取代）
  TECH-OVERVIEW.md               旧技术汇总（23 条规则时代）
  SCRIPT-FORMAT-STRICT.md        中间版格式
  REVIEW-REQUEST-EP1/CODE.md     第 1 集/代码的对外评审请求（已完成）
  TECH-REVIEW-HANDOFF.md         技术评审交接
  TRAE-PROMPT.md / AGENT-GUIDE.md 给外部 agent 的阶段指令
  UI-CONTRACT/UI-DESIGN-BRIEF/UI-SPEC.md   UI 重构前的规格（已重构完）
  DEPLOYMENT-MATRIX.md           旧部署矩阵
  DEEPSEEK-PROMPT*.md            旧提示词模板

_archive/root-legacy/           仓库根目录 11-12 ~ 11-19 的阶段性报告
  （武侠/咖啡馆/配音/UI 重设计/打包计划/验收等 38 份）
```

**归档判据**：被新版取代、或是一次性阶段产物（评审请求、交接、单次实验报告）。
判断不了就先归档——`_archive` 是只进不出的仓库，不是删除。
