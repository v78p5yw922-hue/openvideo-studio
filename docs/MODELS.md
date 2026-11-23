# 模型

本仓库**不包含任何模型权重**。安装器只负责：找到它们、验证大小、告诉 ComfyUI 在哪。

原因：H3 / Music 3 权重的再分发许可尚未确认。请从你获得授权的渠道下载。

---

## 一、三类模型，各自干什么

| 类别 | 模型族 | 干什么 | 谁在用 |
|---|---|---|---|
| **视频** | MiniMax H3（fl2va / ref2va） | 逐镜生成 5/8/10 秒、1344×768 的视频，**自带音轨**（环境声/台词/音效） | 分镜渲染、MV 镜头 |
| **图片** | MiniMax H3 Image | 角色定妆照、场景母版、道具图、文生图工作台 | 资产、文生图 |
| **音乐** | MiniMax Music 3 | 整段 BGM / 带人声的歌（最长 360 秒） | 音乐生成、MV 配乐 |
| 文本编码 | Qwen3-VL 32B（H3 专用打包） | 把提示词编码给 H3 的视频/图片模型 | 视频 + 图片 |

> 注意：**视频和图片共用同一个 Qwen3-VL 文本编码器**；音乐用自己的文本编码器，不共用。

---

## 二、文件清单

### 视频（必需 ≈ 48 GB）

| 文件 | 作用 | 大小 |
|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 主模型（首尾帧 → 视频+音轨） | 19.53 GB |
| `minimax_h3_ref2va_pruned_int8_convrot.safetensors` | 主模型（9 张参考图 → 视频+音轨，cast 镜用） | 19.53 GB |
| `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 文本/视觉编码器（视频与图片共用） | 25.28 GB |
| `minimax_h3_video_vae_int8_convrot.safetensors` | 视频 VAE（省显存） | 2.62 GB |
| `minimax_h3_video_vae_fp16.safetensors` | 视频 VAE（画质优先，二选一） | 4.85 GB |
| `minimax_h3_audio_vae_fp32.safetensors` | **音频 VAE**：把视频模型的音频潜变量解码成波形 | 0.56 GB |

### 图片（必需 ≈ 21 GB）

| 文件 | 作用 | 大小 |
|---|---|---|
| `minimax_h3_image_pruned_int8_convrot.safetensors` | 图像主模型（6 步出图） | 19.54 GB |
| `minimax_h3_image_vae_fp16.safetensors` | 图像 VAE | 1.09 GB |

### 音乐（必需 ≈ 11 GB）

| 文件 | 作用 | 大小 |
|---|---|---|
| `minimax_music3_dit_int8_convrot.safetensors` | 音乐 DiT（去噪主干） | 2.33 GB |
| `minimax_music3_text_encoder_pruned_int8_convrot.safetensors` | 音乐文本编码器（吃 caption/lyrics） | 8.57 GB |
| `minimax_music3_dav.safetensors` | 音频解码器（DAV） | 0.20 GB |

### 可选

| 文件 | 作用 | 大小 |
|---|---|---|
| `open_h3_regenerate_2k_r256pp_bf16.safetensors` | 2K 重绘 LoRA | ~3.6 GB |
| `h3_latent_upscaler.safetensors` | 潜空间超分 | ~348 MB |
| `qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | 4bit 文本编码器（显存更省，替代 int8 版） | 14.61 GB |

---

## 三、参数（写进代码的默认值）

| 项 | 视频 | 图片 | 音乐 |
|---|---|---|---|
| 分辨率/长度 | 1344×768；5/8/10 秒（124/192/243 帧，`17k+5`） | 1024²–2048²（32 的倍数） | 任意时长 ≤ 360 秒 |
| 步数 | 20 | 6 | 30 |
| 采样器 | 模型内置 | 模型内置 | `euler` / `simple`，cfg 1.7，top_k 50 |
| 单次耗时 | 5s≈9 分 · 8s≈17 分 · 10s≈27 分（4090） | ≈30–40 秒 | ≈40–60 秒 / 35 秒音乐 |
| 显存峰值 | 16–23.5 GB | ≈20 GB | ≈12–15 GB |

**三类模型不能同时驻留**：一张 24 GB 卡只够一套。执行器在切换前调用 `/free` 卸载上一套，
所以"渲视频 → 出图 → 做音乐"之间各有 1–2 分钟模型加载。

---

## 四、放置位置

```
ComfyUI/models/
├── diffusion_models/
│   ├── minimax_h3_fl2va_pruned_int8_convrot.safetensors
│   ├── minimax_h3_ref2va_pruned_int8_convrot.safetensors
│   ├── minimax_h3_image_pruned_int8_convrot.safetensors
│   └── minimax_music3_dit_int8_convrot.safetensors
├── text_encoders/
│   ├── qwen3vl_32b_minimax_h3_int8_convrot.safetensors
│   └── minimax_music3_text_encoder_pruned_int8_convrot.safetensors
├── vae/
│   ├── minimax_h3_video_vae_int8_convrot.safetensors
│   ├── minimax_h3_audio_vae_fp32.safetensors
│   ├── minimax_h3_image_vae_fp16.safetensors
│   └── minimax_music3_dav.safetensors
├── loras/open_h3_regenerate_2k_r256pp_bf16.safetensors
└── latent_scale_models/h3_latent_upscaler.safetensors
```

**但你不必这么摆。** 安装器按"文件名 + 大小"匹配，只要在同一个根目录下（任意子目录）就行：

```bash
./ov install --only models --models-dir /data/whatever/models
```

或用 ComfyUI 原生的 `extra_model_paths.yaml`：

```yaml
搭象工坊:
  base_path: /data/models
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
  loras: loras
```

---

## 五、查找顺序

```
--models-dir  >  OV_MODELS_DIR  >  ~/.搭象工坊/state.json 里记录的
              >  <ComfyUI>/models  >  扫描 ~/.cache、/mnt/d、/mnt/e、D:\
```

找到后按 **文件名 + 精确大小（±1%）** 判定命中。`--strict` 才会算 sha256（57 GB 很慢，默认不算）。

---

## 六、体检

`./ov doctor`（或页面「设置 → 环境体检」）会逐项检查：

```
① 配置文件      config.toml 是否存在、能否解析
② 引擎在线      ComfyUI :8288 /system_stats
③ 15 个必需节点  H3 视频、H3 图像、Music 3 各节点
④ 权重文件      上表的必需项，逐个大水校验
⑤ ffmpeg        合片/转码用
⑥ 数据目录      可写（takes/、generated/、beds/）
⑦ GPU           可见、显存 ≥ 20 GB
```

任一项缺失都会给出"怎么修"，不会让你渲到一半才发现。
