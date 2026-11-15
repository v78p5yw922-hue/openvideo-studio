# 给 AI Agent 的使用说明

这个项目的操作者可能是一个 Agent。本文说明**哪些步骤可以无人值守、哪些必须有人参与**，
以及每一步的判定依据（不接受"看起来成功了"这种结论）。

---

## 一、边界：什么能自动，什么不能

| 步骤 | 谁能做 | 判定依据 |
|---|---|---|
| 装 Python 依赖 / 拉 ComfyUI | **Agent 全自动** | `ov status` 各阶段 `done` |
| 扫描并复用已有模型 | **Agent 全自动** | `ov models` 每行 ✓ |
| 起服务、看端口 | **Agent 全自动** | `ov selftest` 返回 0 |
| 导入剧本、调接口 | **Agent 全自动** | `/api/import` 返回 200 且无阻断 |
| 生成计划、读阻断项 | **Agent 全自动** | `plan.blocking == []` |
| **确认真实渲染** | **必须人确认** | 见下方"渲染闸门" |
| 提供模型权重 | **必须人参与** | 许可问题，Agent 不得代为下载 |
| 审核画面好不好 | **必须人判断** | Agent 只能给客观指标（相似度/时长/分辨率） |
| 合成、导出、备份 | **Agent 全自动** | 输出文件存在且 ffprobe 通过 |

---

## 二、渲染闸门（最重要的一条）

真实渲染一镜要 **7–8 分钟 GPU**，8 镜就是 1 小时。因此：

```
✅ 允许：计划 → 展示给用户 → 用户明确说"执行" → confirm
❌ 禁止：Agent 自己 plan + confirm 连点，把别人的显卡占满
```

Agent 必须把 `plan` 的内容（镜头数、预计分钟、阻断项）**原样呈现**，等一句明确的许可。

例外：用户已经说过"你自己跑完不用问我"——那也算明确许可，但仍要在开始前把计划贴出来。

---

## 三、标准操作序列

```bash
# 1. 体检（只读）
./ov doctor --models-dir /data/models || exit 1

# 2. 导入剧本
curl -s -X POST localhost:8801/api/import \
  -H 'content-type: application/json' \
  -d "$(jq -Rn --rawfile md examples/hijack_8.md '{project_id:"demo",markdown:$md}')"

# 3. 计划（把输出原样给用户看）
curl -s -X POST localhost:8801/api/projects/demo/plan -H 'content-type: application/json' -d '{}'

# 4. 等用户确认后
curl -s -X POST localhost:8801/api/projects/demo/confirm \
  -H 'content-type: application/json' -d '{"plan_id":"<上一步返回>"}'

# 5. 轮询（不要 sleep 死等，看 state）
watch -n 10 'curl -s localhost:8801/api/projects/demo | jq ".counts"'

# 6. 合成
curl -s -X POST localhost:8801/api/projects/demo/assemble -H 'content-type: application/json' -d '{}'
```

---

## 四、客观质量指标（Agent 能自证的部分）

渲染完不要只说"完成了"，给出可验证的数字：

| 指标 | 怎么算 | 阈值 |
|---|---|---|
| 时长 | `ffprobe -show_entries format=duration` | 每镜 5.17 s ± 0.1 |
| 分辨率 | `ffprobe -show_entries stream=width,height` | 1344×768 |
| **静止检测** | 每镜首帧与末帧 SSIM | **> 0.95 判为"疑似静止"，必须报告** |
| 镜头间重复 | 相邻镜首帧 SSIM | > 0.9 说明 chain 用错（应 cut） |
| 缺片段 | 每个 shot 是否有 take | 有缺口不得合成 |

静止检测的参考实现（本项目实测出来的教训）：

```bash
ffmpeg -y -i take.mp4 -frames:v 1 f.jpg
ffmpeg -y -sseof -0.15 -i take.mp4 -frames:v 1 l.jpg
ffmpeg -i f.jpg -i l.jpg -filter_complex "[0:v]scale=384:216[a];[1:v]scale=384:216[b];[a][b]ssim" -f null - 2>&1 | grep All
```

---

## 五、Agent 不得做的事

1. **不要替用户下载模型权重。** 许可未确认，且 57 GB 下载会打爆用户流量。
2. **不要跳过 `plan` 直接 `confirm`。**
3. **不要删用户的 take / 项目**，除非用户明确要求。
4. **不要在 `doctor` 报错时继续往下跑**——先把缺失项报给用户。
5. **不要美化结论。** 画面不合格就说"画面不合格"，并给出上面那张表的数字。

---

## 六、常见任务的命令清单

| 任务 | 命令 |
|---|---|
| 看还剩哪些镜头没渲 | `curl -s localhost:8801/api/projects/demo \| jq '.shots[] \| select(.take.version == null) \| .shot_id'` |
| 停掉正在渲的一镜 | `curl -s -X POST localhost:8801/api/jobs/<jid>/cancel` |
| 全部停 | `curl -s -X POST localhost:8801/api/stop` |
| 改转场（换人→cut） | `curl -s -X POST localhost:8801/api/projects/demo/shots/auto-transition` |
| 确认某条片段有效 | `curl -s -X POST localhost:8801/api/projects/demo/shots/s03/adopt` |
| 跑不变量自检 | `curl -s localhost:8801/api/selftest` |
| 导出 EDL | `curl -s -o cut.edl localhost:8801/api/projects/demo/export/edl` |

---

## 七、故障时的正确动作

| 症状 | Agent 应做 | 不应做 |
|---|---|---|
| `plan` 有阻断 | 把阻断项原文贴给用户 | 自己改剧本绕过 |
| 渲染 `rc=1 status=error` | 查是否 OOM；是则等重试结果 | 立刻重排整个队列 |
| 队列卡在 `running` | 跑 `ov doctor`，查是否有重复进程 | 直接删数据库记录 |
| 合成报"这些镜头还没有片段" | 列出缺口镜头 | 跳过后合并 |
