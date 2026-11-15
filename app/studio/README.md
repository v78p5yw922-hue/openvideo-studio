# OpenVideo Studio · 生产线（v2）

从零起的一套生产平台，**不复用**旧资产、旧剧本、旧片段。

```
studio/
├── core/        schema.sql / db.py / model.py / service.py
├── exec/        runner.py（执行器，默认关闭）
├── api/         app.py（FastAPI）
├── web/         index.html（单页 UI，无构建步骤）
└── tests/       test_core.py / test_api.py
```

## 现在能做什么（M1 + 导入）

| 能力 | 状态 |
|---|---|
| 项目 | 新建、选择、看统计 |
| **剧本导入** | 粘贴 DeepSeek 的 Markdown → 自动建项目/角色/场景/镜头 |
| 资产 | 角色（上传参考图）、场景（上传母版图） |
| 镜头 | 单栏卡流、状态色、缩略图、逐镜检查器编辑 |
| 绑定 | 点左侧角色/场景 → 指派给选中镜头 |
| 单镜生成 | 检查器里「生成这一镜」（走计划+确认，不绕过确认门） |
| 指纹 | 改任何输入 → 指纹变 → take 变 `stale` |
| 级联 | `chain` 镜头上游一变，整条链自动失效（含递归） |
| 计划 | 镜数 / 预计分钟 / 完成时刻 / 阻断与警告 |
| 确认门 | 计划 id 只在输入未变时有效；输入一变，确认直接 409 |
| 执行器 | 默认 `STUDIO_ALLOW_RENDER=0`，确认只排队不渲染 |
| 审片 | 通过 / 打回（打回原因），只对最新 take 生效 |
| **执行器** | `queued → running → done/failed`；take 命名 `s01_v1.mp4`；回填耗时与峰值显存 |
| **预览首帧** | 单镜 3 秒静帧（约 2–3 分钟），不产生 take、不改状态，只供看构图 |
| **草稿渲染** | 480×840 / 12 步，勾选「草稿」即走这条路径 |
| **跑完一镜暂停** | 渲完一镜把剩余镜头重新排队，任务状态 `paused` |
| **审片页** | `/review` 逐镜播放、通过 / 打回（原因），只显示待审镜头 |
| **自检页** | `/selftest` 在真实数据库上验证 9 条不变量，浏览器里可复现 |
| **备份** | 每日 03:20 把 SQLite 快照与资产同步到 `D:\openvideo-backups\studio`，保留 14 份 |
| **导出时间线** | `导出 EDL` / `导出 OTIO`，只导出"已通过"的 take，缺镜会列出来 |
| **取消任务** | 单个取消、清空排队；取消后镜头回到"未生成" |
| **资产锁定** | 场景母版 / 角色主图可锁定；锁定时替换返回 409 |
| **资产版本** | 每次替换归档旧图（`asset_versions`），可恢复到任意历史版本 |
| **实时进度** | 执行器流式读渲染输出 + 每 2 秒抓 ComfyUI 日志的 `42%`，镜头卡显示百分比 |
| **生产统计** | 通过率、重渲率、均耗时、峰值显存、GPU 分钟、打回原因分布 |
| **OTIO 往返校验** | 导出文件用官方 `opentimelineio` 读回，断言剪辑结构、时长、连续性 |
| **拖拽绑定** | 角色/场景卡可直接拖到镜头卡；落点高亮；5 秒内可撤销；超过 3 个角色会被拒 |
| **指派接口** | `POST /shots/{sid}/assign`，原子更新并重算指纹（改了就 stale） |
| **排序拖拽** | 拖镜头卡到另一张卡前即插入；`cut` 镜头不失效，`chain` 因上游变化而失效并给出警告 |
| **全局队列提示** | 顶栏显示「GPU 忙：项目 镜头」，避免两个项目同时点确认而不自知 |
| **批量编辑** | 勾选多镜 → 底部批量条改场景/转场，一次落库、指纹重算一次 |
| **版本恢复 UI** | 资产行「版本」→ 列表 → 恢复任意历史母版（恢复本身也记一版） |
| **Markdown 导出** | 导出与导入同格式；往返一次结构不变（有幂等测试） |
| **导入预检** | 覆盖前先算「哪些镜头会失效 / 新增 / 删除」，UI 二次确认；名字与自定义 ID 都能映射回原资产 |

### 剧本导入能认什么

- `## 项目` 里的 `片名` / `视觉风格`
- `## 世界设定` 里的 `视觉语言`
- `## 角色卡`：扁平列表（`- 名字：`）或 `### 小标题` 两种写法
- `## 场景卡`：`### 场景 N：名字` + `机位` / `科技物件|固定物件` / `光源|光线`
- `## 镜头表`：Markdown 表格，列名含"镜号/场景/出场角色/景别/台词/转场"即可
- `## 每镜 prompt`：`### 镜 N` 下面的整段文字（没有也接受，会警告）

中文名会生成稳定哈希 ID（如 `scene_6bdb9f`），同名永远同 ID。

## 六状态

```
empty 灰 · queued 黄 · running 蓝 · review 紫 · passed 绿 · rejected 红
（另有 stale 橙：take 还在，但输入已变）
```

## 关键设计

**镜头身份是 `shot_id`，不是位置。** 片段存 `takes/<shot_id>_v<n>.mp4`，
拖顺序不会串片。

**指纹折进上游。** `chain` 镜头的指纹里包含上一镜的指纹，
所以改 s03 会一路失效到链尾，不需要手工标记。

**状态不落盘。** 由 take / review / job 派生，避免多处写入不一致。

## 启动

```bash
# 服务（systemd）
sudo systemctl status studio.service
sudo systemctl restart studio.service

# 开发模式
cd ~/openvideo/video-agent
.venv/bin/uvicorn studio.api.app:app --host 127.0.0.1 --port 8801 --reload
```

打开 http://127.0.0.1:8801

## 打开真实渲染（需用户明确同意）

```bash
sudo systemctl edit studio.service     # 加 Environment=STUDIO_ALLOW_RENDER=1
sudo systemctl restart studio.service
```

没有这一步，**确认计划也只会排队，不会动 GPU**。

关闭（恢复到"只排队"）：

```bash
sudo systemctl edit studio.service --full   # 删掉那一行
sudo systemctl restart studio.service
```

## 执行器怎么跑

```
jobs(state=queued) → tick() 取一条
  ├─ render : 写 render.json → render_drama --only <sid> [--steps 12 --width 480 若草稿]
  │            → 把 000N.mp4 移到 takes/<sid>_v<n>.mp4
  │            → 记录 take(fingerprint=计划时的指纹, elapsed, peak_mib)
  └─ preview: render_still → previews/<sid>.jpg（不产生 take）
```

一次只跑一镜，跑完立刻落库——**中断最多损失一镜**。

## 无 GPU 测试执行器

```bash
STUDIO_ALLOW_RENDER=1 \
STUDIO_RENDER_CMD=studio/tests/fake_render.py \
STUDIO_STILL_CMD=studio/tests/fake_still.py \
.venv/bin/python -m pytest studio/tests -q
```

假渲染器会写一个占位 mp4 并打印 `peak=… MiB`，用来验证命名、版本、
指纹回写、峰值解析这些逻辑，全程不碰显卡。

## 测试

```bash
.venv/bin/python -m pytest studio/tests -q
```

覆盖：母版变化使全片失效、`cut` 不传播 / `chain` 传播、
状态生命周期（empty→review→passed→stale）、打回后版本递增、
计划计数与阻断、旧计划 409、审片前置校验。

### 端到端演练（无 GPU）

`tests/test_e2e_fake.py` 用假渲染器走完整条路：

```
导入 Markdown → 上传母版 → 计划 → 确认 → 执行器渲染 → 审片通过
→ 导出 EDL/OTIO → 备份快照
```

断言包括：take 名 `s0N_v1.mp4`、峰值显存回填、EDL 帧数 360、OTIO 三段、备份含资产。

### 两个真实 bug（由这套测试抓出）

1. **执行器用旧指纹记 take**：母版在排队期间被换掉，渲完立刻变 `stale`。
   修法：执行器写入前先 `recompute_fingerprints`。
2. **部分更新抹掉 `anchor`**：PUT 只传部分字段时，`anchor/duration/sound` 被清空，
   导致上游镜头指纹变化、误判 stale。修法：缺省字段沿用数据库旧值。

## 下一步（M2）

1. 剧本导入（JSON / Markdown 表格 → 镜头列表）
2. 草稿预览（首帧 / 低清），单镜 1–2 分钟
3. 执行器接真实渲染器（`--only <shot_id>` + take 命名 + 峰值显存回填）
4. 批次执行与"跑完这一镜暂停"
5. 审片页（多镜连续播、打回原因统计）
6. 备份（每日 rsync `studio-data/` 到 D 盘）
