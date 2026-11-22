# 部署

单机部署，两个进程：引擎（ComfyUI，:8188）与工作台（:8801）。都用 systemd 托管。

---

## systemd

```bash
ROOT=$PWD
sed -e "s|%USER%|$USER|g" \
    -e "s|%VENV%|$ROOT/.venv|g" \
    -e "s|%COMFYUI_DIR%|/path/to/ComfyUI|g" \
    services/搭象工坊-engine.service.in | sudo tee /etc/systemd/system/搭象工坊-engine.service

sed -e "s|%USER%|$USER|g" \
    -e "s|%VENV%|$ROOT/.venv|g" \
    -e "s|%APP_DIR%|$ROOT/app|g" \
    -e "s|%DATA_DIR%|$HOME/搭象工坊/studio-data|g" \
    services/搭象工坊-studio.service.in | sudo tee /etc/systemd/system/搭象工坊-studio.service

sudo systemctl daemon-reload
sudo systemctl enable --now 搭象工坊-engine 搭象工坊-studio
```

### 关键环境变量

| 变量 | 作用 | 默认 |
|---|---|---|
| `STUDIO_ALLOW_RENDER` | **必须为 1 才会真正渲染**（安全默认：关） | 0 |
| `STUDIO_DATA` | 数据库与产物的根目录 | `~/搭象工坊/studio-data` |
| `STUDIO_COMFY_LOG` | 引擎日志路径，用于解析进度 | 自动探测 |
| `STUDIO_RENDER_CMD` | 覆盖渲染命令（测试用） | `scripts/render_drama.py` |
| `PATH` | **WSL 下必须包含 `/usr/lib/wsl/lib`**，否则 `nvidia-smi` 采集显存失败 | — |

> `STUDIO_ALLOW_RENDER=0` 时，计划/确认照常工作，但队列不会执行——适合"先排好班，再人工开闸"。

---

## 端口

| 端口 | 服务 | 绑定 |
|---|---|---|
| 8188 | ComfyUI 引擎 | 127.0.0.1 |
| 8801 | 工作台 | 127.0.0.1 |

两个都只监听回环。**不要直接暴露公网**（无鉴权）。需要远程访问就用 SSH 隧道：

```bash
ssh -L 8801:127.0.0.1:8801 user@host
```

---

## 数据目录

```
$STUDIO_DATA/
├─ studio.db                     # SQLite（WAL）
├─ projects/<pid>/
│  ├─ takes/s01_v1.mp4           # 每镜的每个版本
│  ├─ previews/s01.jpg           # 首帧预览
│  ├─ firstframes/               # 指定首帧
│  ├─ scenes/plate.jpg           # 场景母版
│  ├─ assemble/<ts>/             # 合成时的暂存（软链）
│  └─ output/<pid>_<ts>.mp4      # 成片
├─ characters/<cid>_ref.jpg      # 角色参考图
└─ tmp/<job_id>/<shot>/          # 渲染工作目录（用完即删）
```

**备份只认 `studio.db` + `projects/` + `characters/`**，其余都是可重建的。

---

## 备份

本项目自带一个轻量备份脚本：

```bash
# 每日 03:28 执行：SQLite .backup + rsync，保留 14 份
app/studio/backup.py --to /mnt/backup/搭象工坊 --keep 14
```

配 systemd timer 或直接 cron：

```cron
28 3 * * * /path/to/.venv/bin/python /path/to/app/studio/backup.py --to /mnt/backup/搭象工坊
```

恢复：停服务 → 用备份覆盖 `studio.db` 与 `projects/` → 起服务 → 跑 `GET /api/selftest`。

---

## 升级

```bash
git pull
./ov install --only venv --force --yes     # 依赖可能变了
sudo systemctl restart 搭象工坊-studio
curl -s localhost:8801/api/selftest | jq .summary
```

数据库迁移在启动时自动执行（`core/db.py::_migrate`，只加列不删列）。

---

## 排障

| 现象 | 查什么 |
|---|---|
| 页面 404/500 但代码是新的 | 部署时先复制再重启：`cp -r ... && rm -rf **/__pycache__ && systemctl restart` |
| 队列显示有任务但不动 | `STUDIO_ALLOW_RENDER` 是否为 1；`pgrep -af uvicorn` 是否有多个进程 |
| 进度一直 0% | `STUDIO_COMFY_LOG` 路径是否指向引擎日志 |
| 显存峰值显示 0 | PATH 缺 `/usr/lib/wsl/lib` |
| 合成报缺片段 | `curl -s localhost:8801/api/projects/<id> \| jq '.shots[] \| select(.take.version==null)'` |
