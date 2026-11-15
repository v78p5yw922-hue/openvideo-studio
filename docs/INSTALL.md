# 安装

三种场景，按你的现状选一种。所有命令都可以**重复执行**，不会把已装好的东西弄坏。

---

## 场景 A：已有 ComfyUI + 模型（最省事，0 下载）

```bash
git clone <this-repo> openvideo
cd openvideo
bash install.sh --comfyui-dir /path/to/ComfyUI --models-dir /path/to/ComfyUI/models
```

安装器会：

1. 检查 python / GPU / ffmpeg
2. 建 `.venv` 并装 6 个 Python 依赖（约 1 分钟）
3. **复用**你已有的 ComfyUI，不克隆
4. 检查 `H3CastMember` / `H3VideoUpsampler` 节点是否在
5. 按**文件名 + 大小**在你的 models 目录里找 5 个权重
6. 打印还缺什么

---

## 场景 B：全新机器（拉代码，不拉权重）

```bash
bash install.sh
```

它会克隆 ComfyUI（钉在 `v0.30.0`），装依赖，然后**停下来告诉你缺权重**。
这是故意的：H3 权重许可未确认，本仓库不代为分发。

拿到权重后：

```bash
./ov install --only models --models-dir /data/models
./ov selftest
```

---

## 场景 C：只想体检

```bash
./ov doctor --models-dir /data/models
```

输出形如：

```
OpenVideo doctor  v0.1.0   (Linux 5.15)
  ✓ Python 3.10.12
  ✓ GPU: NVIDIA GeForce RTX 4090, 24564 MiB, 572.xx
  ✓ ffmpeg: ffmpeg version 5.1.6
  ✓ ComfyUI: /home/me/ComfyUI  (0.30.0)
  ✓ 节点 ComfyUI-H3-Cast
  ! 节点 H3-Video-Upsampler 缺失（角色一致性/超分不可用）
  ✓ 模型目录: /data/models
  ✓ minimax_h3_fl2va_pruned_int8_convrot.safetensors  主模型（必需）
  ✓ qwen3vl_32b_minimax_h3_int8_convrot.safetensors   文本编码器（必需）
  ...
结论
  可用但有缺口：H3-Video-Upsampler
```

`doctor` **只读**，不会创建、移动、删除任何文件。

---

## 阶段与续跑

阶段固定为：

```
config → venv → comfyui → nodes → models → services → selftest
```

| 命令 | 作用 |
|---|---|
| `ov install` | 预演（不执行） |
| `ov install --yes` | 全流程 |
| `ov install --only models --yes` | 只处理模型 |
| `ov install --resume` | 从失败处继续 |
| `ov install --force --only venv` | 重做某个阶段 |
| `ov status` | 看每个阶段的状态与时间 |

状态在 `~/.openvideo/state.json`，原子写入（先写 `.tmp` 再 rename），不会出现半个 JSON。

---

## 服务

Linux/WSL2 用 systemd：

```bash
sed -e "s|%USER%|$USER|g" \
    -e "s|%VENV%|$PWD/.venv|g" \
    -e "s|%APP_DIR%|$PWD/app|g" \
    -e "s|%DATA_DIR%|$HOME/openvideo/studio-data|g" \
    -e "s|%COMFYUI_DIR%|/path/to/ComfyUI|g" \
    services/openvideo-studio.service.in | sudo tee /etc/systemd/system/openvideo-studio.service

sudo systemctl daemon-reload
sudo systemctl enable --now openvideo-engine openvideo-studio
```

不想用 systemd：`./ov start --foreground`。

---

## 排错表

| 现象 | 原因 | 处理 |
|---|---|---|
| `找不到 nvidia-smi` | 驱动没装，或 WSL 没挂载 | 装驱动；WSL 下确认 `/usr/lib/wsl/lib/nvidia-smi` 存在 |
| 装依赖卡在 torch | 默认源慢 | `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple` |
| `节点 H3CastMember 缺失` | 自定义节点没放进去 | 从权重包解压到 `<ComfyUI>/custom_nodes/` |
| 能起服务但生成报 `status=error` | 权重没加载 / 显存不足 | `ov doctor` 看权重命中；换 int8 VAE 或降分辨率 |
| 渲一镜就 OOM | 分辨率/步数过高 | 1344×768 改 896×512，或步数 20→16 |
| 画面几乎不动 | 用了 `chain` 但角色变了 | 改成 `cut`（界面「按角色自动设转场」） |
| 合并后没声音 | 片段无音轨 | 正常现象：H3 不产音频，声音需后期 |
| 端口被占用 | 旧进程没退 | `ss -lntp \| grep 8801`，杀掉；`ov doctor` 会提示未监听 |

---

## 卸载

```bash
sudo systemctl disable --now openvideo-studio openvideo-engine
rm -rf ~/.openvideo           # 状态与日志
rm -rf ./app ./engine .venv   # 代码与虚拟环境
```

模型与 ComfyUI 目录不在本仓库内，不会被删除。
