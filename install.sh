#!/usr/bin/env bash
# OpenVideo Studio — 一键安装入口
#   用法：bash install.sh --models-dir /path/to/models
# 全部逻辑在 ./ov（Python，无第三方依赖），这里只负责找到 python3。
set -euo pipefail

cd "$(dirname "$0")"

PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "找不到 python3（需要 3.10+）。" >&2
  echo "  Ubuntu/Debian: sudo apt-get install -y python3 python3-venv ffmpeg" >&2
  echo "  macOS:         brew install python@3.11 ffmpeg" >&2
  exit 1
fi

ver=$("$PY" -c 'import sys;print("%d.%d" % sys.version_info[:2])')
case "$ver" in
  3.1[0-9]) ;;
  *) echo "Python $ver 太旧，需要 3.10+。" >&2; exit 1 ;;
esac

echo "== 体检（不改任何东西）=="
"$PY" ./ov doctor "${OV_MODELS_DIR:+--models-dir "$OV_MODELS_DIR"}" || true

echo
echo "== 开始安装 =="
exec "$PY" ./ov install --yes "$@"
