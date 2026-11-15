# 模型

本仓库**不包含任何模型权重**。安装器只负责：找到它们、验证大小、告诉 ComfyUI 在哪。

原因：H3 权重的再分发许可尚未确认。请从你获得授权的渠道下载。

---

## 清单

| 文件 | 作用 | 大小 | 必需 |
|---|---|---|---|
| `minimax_h3_fl2va_pruned_int8_convrot.safetensors` | 主模型（扩散） | ~20 GB | ✅ |
| `qwen3vl_32b_minimax_h3_int8_convrot.safetensors` | 文本/视觉编码器 | ~26 GB | ✅ |
| `minimax_h3_video_vae_fp16.safetensors` | 视频 VAE（画质优先） | ~4.9 GB | 二选一 |
| `minimax_h3_video_vae_int8_convrot.safetensors` | 视频 VAE（省显存） | ~2.7 GB | 二选一 |
| `open_h3_regenerate_2k_r256pp_bf16.safetensors` | 2K 重绘 LoRA | ~3.6 GB | 可选 |
| `h3_latent_upscaler.safetensors` | 潜空间超分 | ~348 MB | 可选 |

合计约 **57 GB**。

---

## 放置位置

ComfyUI 期望的目录结构：

```
ComfyUI/models/
├─ diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
├─ text_encoders/qwen3vl_32b_minimax_h3_int8_convrot.safetensors
├─ vae/minimax_h3_video_vae_fp16.safetensors
├─ loras/open_h3_regenerate_2k_r256pp_bf16.safetensors
└─ latent_upscale_models/h3_latent_upscaler.safetensors
```

**但你可以不这么摆。** 安装器按"文件名 + 大小"匹配，只要在同一个根目录下（任意子目录）就行：

```bash
./ov install --only models --models-dir /data/whatever/models
```

或者用 ComfyUI 原生的 `extra_model_paths.yaml` 指向别处：

```yaml
openvideo:
  base_path: /data/models
  diffusion_models: diffusion_models
  text_encoders: text_encoders
  vae: vae
  loras: loras
```

---

## 查找顺序

```
--models-dir  >  OV_MODELS_DIR  >  ~/.openvideo/state.json 里记录的
              >  <ComfyUI>/models  >  扫描 ~/.cache、/mnt/d、/mnt/e、D:\
```

找到后按 **文件名 + 精确大小（±3%）** 判定命中。`--strict` 才会算 sha256（57 GB 很慢，默认不算）。

---

## 显存档位建议

| 显存 | 分辨率 / 步数 | VAE |
|---|---|---|
| 24 GB | 1344×768 / 20 | fp16 |
| 20 GB | 1344×768 / 16 | int8 |
| 16 GB | 896×512 / 16 | int8 |
| 12 GB | 512×288 / 12（未验证） | int8 |

改法：`app/scripts/render_drama.py` 里 `RENDER_PARAMS_DEFAULT`，或在 `studio/core/model.py` 覆盖。
界面上的「草稿」勾选会用 840×480 / 12 步。

---

## 自定义节点

| 节点 | 提供 | 必需 | 缺失后果 |
|---|---|---|---|
| `ComfyUI-H3-Cast` | `H3CastMember` | ✅ | 角色参考图不生效，只剩纯文生视频 |
| `H3-Video-Upsampler` | `H3VideoUpsampler` | — | 不能 2K 超分；用 `--no-upscale` 出 1344×768 |

两个节点随权重包分发，不在公开仓库。解压到 `<ComfyUI>/custom_nodes/`，重启引擎。

验证：

```bash
curl -s localhost:8188/object_info | jq 'keys[] | select(test("H3"))'
```

应看到 `H3CastMember`、`H3CastToVideo`（或类似）、`H3VideoUpsampler`。
