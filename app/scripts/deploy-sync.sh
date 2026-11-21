#!/usr/bin/env bash
# 把 Windows 侧的源码同步到 WSL 运行目录，并可选地跑测试 / 重启服务。
#
#   bash deploy-sync.sh               同步 + 测试 + 重启 studio
#   bash deploy-sync.sh --no-restart  只同步 + 测试
#   bash deploy-sync.sh --skip-test   同步 + 重启，不跑测试
#   bash deploy-sync.sh --sync-only   只同步
#
# 必须从 WSL 里跑（PowerShell 侧有 deploy-to-wsl.ps1 包装）。

set -uo pipefail

SRC=${SRC:-/mnt/d/AI/DeepSeekProjects/OpenVideo}
DST=${DST:-/home/agent/openvideo/video-agent}
DO_TEST=1
DO_RESTART=1

for arg in "$@"; do
  case "$arg" in
    --no-restart) DO_RESTART=0 ;;
    --skip-test)  DO_TEST=0 ;;
    --sync-only)  DO_TEST=0; DO_RESTART=0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

echo "== 源: $SRC"
echo "== 目标: $DST"

if [ ! -d "$SRC" ]; then
  echo "源目录不存在: $SRC" >&2
  exit 1
fi

# ---- 队列检查：有任务在跑就不许重启
QUEUE=$(curl -s --max-time 5 http://127.0.0.1:8801/api/queue/global || echo '{"count":-1}')
echo "== 队列: $QUEUE"
if [ "$DO_RESTART" = "1" ] && ! printf '%s' "$QUEUE" | grep -q '"count":0'; then
  echo "!! 有任务在跑，自动关闭重启（只同步）"
  DO_RESTART=0
fi

# ---- 同步
for d in studio scripts deploy; do
  [ -d "$SRC/$d" ] || continue
  mkdir -p "$DST/$d"
  cp -a "$SRC/$d/." "$DST/$d/"
  find "$DST/$d" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  echo "   $d 已同步"
done

# 前端产物：先清空再同步，否则旧 hash 的 bundle 会一直堆着（体积越滚越大，也容易看错版本）
if [ -d "$SRC/studio/web/dist" ]; then
  rm -rf "$DST/studio/web/dist"
  mkdir -p "$DST/studio/web/dist"
  cp -a "$SRC/studio/web/dist/." "$DST/studio/web/dist/"
  echo "   web/dist 已重建（旧 bundle 已清理）"
fi

# 实例配置：存在就同步（不存在则沿用 WSL 里已有的那份）
if [ -f "$SRC/config.toml" ]; then
  cp -f "$SRC/config.toml" "$DST/config.toml"
  echo "   config.toml 已同步"
fi

# 规范文档：规范页（/api/spec）要读，随部署一起带过去
SPEC_DIR="$DST/studio/data/spec"
mkdir -p "$SPEC_DIR"
for f in RULES-STRICT.md REVIEW-LESSONS.md SCRIPT-FORMAT-V3.md; do
  [ -f "$SRC/openvideo-release/docs/$f" ] && cp -f "$SRC/openvideo-release/docs/$f" "$SPEC_DIR/$f"
done
echo "   spec 文档已同步"

# 技能（skill）：技能页要展示，随部署带过去
SKILL_SRC="$SRC/.dsh/skills"
if [ -d "$SKILL_SRC" ]; then
  rm -rf "$DST/studio/data/skills"
  mkdir -p "$DST/studio/data/skills"
  cp -a "$SKILL_SRC/." "$DST/studio/data/skills/"
  echo "   skills 已同步"
fi

chown -R agent:agent "$DST/studio" "$DST/scripts" 2>/dev/null || true

# ---- 测试
if [ "$DO_TEST" = "1" ]; then
  echo "== 测试"
  OUT=$(cd "$DST" && sudo -u agent env HOME=/home/agent .venv/bin/python -m pytest studio/tests -q 2>&1 | tail -3)
  echo "$OUT"
  if ! printf '%s' "$OUT" | grep -q 'passed'; then
    echo "!! 测试未通过，不重启服务" >&2
    exit 1
  fi
fi

# ---- 重启
if [ "$DO_RESTART" = "1" ]; then
  echo "== 重启 studio"
  systemctl restart studio.service
  sleep 6
  curl -s -o /dev/null -w '   health %{http_code}\n' http://127.0.0.1:8801/api/health
fi

echo "== 完成（浏览器 Ctrl+Shift+R 硬刷新 http://127.0.0.1:8801）"
