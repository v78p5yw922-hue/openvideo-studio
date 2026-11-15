# 架构

## 组件

| 层 | 目录 | 职责 |
|---|---|---|
| 前端 | `app/studio/web/` | 单页，无构建步骤：生产线、审核页、自检页 |
| API | `app/studio/api/app.py` | 约 40 个路由；`lifespan` 里启动执行器 |
| 业务 | `app/studio/core/service.py` | 项目/资产/镜头/任务/审核的全部规则 |
| 指纹 | `app/studio/core/model.py` | 输入哈希、级联、命名 |
| 解析 | `app/studio/core/script_import.py` | Markdown 剧本 ↔ 数据库 |
| 执行 | `app/studio/exec/runner.py` | 队列、单任务执行、OOM 重试、取消 |
| 渲染 | `app/scripts/render_drama.py` | 组 ComfyUI 工作流、i2v / t2v / 锚定 |
| 后期 | `app/scripts/merge.py`、`finalize.py` | 拼接、烧字幕 |
| 导出 | `app/studio/export/` | EDL、OTIO、Markdown |

## 数据模型（SQLite）

```
projects ─┬─ scenes ──── asset_versions
          ├─ shots ───── takes ──── reviews
          ├─ previews
          ├─ jobs
          └─ events
characters（跨项目共享）─── asset_versions
```

- `shots.cast_json` 存角色 id 数组（≤3）
- `shots.first_frame` 为可选的"指定首帧"（覆盖 chain）
- `takes.kind` 区分 `final` 与 `draft`
- `jobs.kind` 区分 `render` 与 `preview`（**preview 不改变镜头状态**）

## 指纹（核心不变量）

```python
payload = {
  "shot":   {shot_id, scene_id, cast, camera, prompt, dialogue,
             transition, anchor, duration, [first_frame]},
  "scene":  {scene_id, camera, props, light, plate_sha},
  "cast_refs": sorted([每个角色参考图的 sha256]),
  "params": {width, height, steps, seed},
  "chain_from": 上一镜的指纹（仅 chain 时）
}
fingerprint = sha256(canonical_json(payload))
```

三条规则：

1. **可选字段只在被使用时进哈希**（`first_frame` 为空则不加 key）——否则加功能会让所有历史 take 误判失效（这个坑真踩过）
2. **chain 折叠上游指纹** → 改上游自动级联失效下游
3. **take 记录当时的指纹** → 现在的指纹 ≠ take 的指纹即为 `stale`

## 状态机

```
shot:  empty → queued → running → review → passed / rejected
                                  ↘ stale（输入变了）
job:   queued → running → done / failed / cancelled / paused
```

`review` 只在"有 final take 且未 stale"时出现；草稿 take 不参与最终判定。

## 队列

- 一次只跑一个任务（单卡）
- **原子认领**：`UPDATE ... WHERE state='queued' AND NOT EXISTS(running)`
  防止两个服务进程（重启残留）同时消费
- 启动时把遗留的 `running` 标记为失败（`fail_orphan_running`）
- 一镜一任务：可单独取消、单独看进度

## 渲染路径

```
shot.transition == "chain" 且有上游 take
      └─▶ 复制上游 take 到工作目录 000N.mp4 → 取末帧 → i2v
shot.first_frame 有值
      └─▶ 直接拿这张图当首帧（覆盖 chain）
shot.anchor == "plate"（只对空镜有效）
      └─▶ 场景母版当首帧
否则
      └─▶ 纯文生视频（t2v）+ 角色参考图
```

⚠️ **chain 的代价**：首帧被逐像素锁死，换人/大动作时模型只能微动 → 整片静止。
所以"角色集合发生变化"时应使用 `cut`（界面有「按角色自动设转场」）。

## 两段式确认

```
POST /plan    → 计算待渲镜头、预计耗时、阻断项；存 PLAN_STORE（TTL 600s）→ plan_id
POST /confirm → 用 plan_id 取回当初的镜头集合，重新校验，逐镜入队
```

重新校验是必要的：计划到确认之间用户可能改了东西，此时应要求重新计划，而不是拿旧清单去渲。

## 自检（不变量）

`GET /api/selftest` 跑 9 条，任何一条失败都说明核心假设被破坏：

1. 改字段 → 指纹变
2. 上游变 → 下游 stale
3. `cut` 不级联
4. 草稿 take 不产生 `passed`
5. 审核写回版本
6. 计划集合 = 待渲集合
7. 删除 take 后状态回退
8. 导入导出往返不丢字段
9. 拼接产物可被 ffprobe 读出

## 为什么不用消息队列 / 不用 Redis

单机、单卡、单用户。SQLite + 一个线程足够，而且**少一个组件就少一个坏掉的地方**。
真要扩展到多卡，认领逻辑（`NOT EXISTS(running)`）已经是向"任务表 + 多 worker"演进的最小雏形。
