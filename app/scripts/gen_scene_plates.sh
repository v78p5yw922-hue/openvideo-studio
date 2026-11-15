#!/usr/bin/env bash
# Generate clean scene plates (no people) for the three spaces.
set -u
V="$HOME/openvideo/video-agent"
L="$V/drama/library/scenes"
TMP="$V/segments/_plates"
cd "$V" || exit 1
mkdir -p "$TMP"

gen() {
  local sid="$1" prompt="$2"
  local d="$L/$sid"
  mkdir -p "$d" "$TMP/$sid"
  [ -s "$d/plate_1.jpg" ] && [ "${FORCE:-0}" != "1" ] && { echo "  $sid: exists, skip"; return; }
  echo "=== $sid $(date +%H:%M:%S) ==="
  .venv/bin/open-video "$prompt" --aspect 9:16 --duration 5 \
    --output "$TMP/$sid/raw.mp4" > "$TMP/$sid/log.txt" 2>&1
  if [ ! -s "$TMP/$sid/raw.mp4" ]; then echo "  FAILED"; tail -3 "$TMP/$sid/log.txt"; return; fi
  ffmpeg -y -v error -i "$TMP/$sid/raw.mp4" -ss 2.2 -frames:v 1 -update 1 -q:v 3 "$d/plate_1.jpg"
  python3 - "$d" <<'PY'
import json, sys
from pathlib import Path
d = Path(sys.argv[1])
card = json.loads((d / "card.json").read_text(encoding="utf-8"))
card["images"] = ["drama/library/scenes/%s/plate_1.jpg" % d.name]
card["placeholder"] = False
(d / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
PY
  echo "  ok -> $d/plate_1.jpg"
}

gen scene_hutong_shuxia "空镜，无人。北京老胡同中段，大槐树下，藤椅与小马扎，搪瓷缸在藤椅扶手，小桌上放着切开的一半西瓜，背后灰砖墙与远处红漆门，午后四点半暖白偏金光，树荫碎光斑，35mm胶片质感，浅景深，9:16竖屏。"
gen scene_hutong_menkou "空镜，无人。北京老胡同中段，红漆院门半开，木门槛，门环，上方晾衣绳挂着一件白衬衫，墙根堆着蜂窝煤，青砖地面，门外强烈日光，明暗交界在门槛，35mm胶片质感，9:16竖屏。"
gen scene_hutong_wunei "空镜，无人。老北京堂屋室内，窗帘半拉，摇头电扇在桌上，木桌上放着切开的西瓜，纱窗，门缝一道白光照在地面，室内暗调，35mm胶片质感，9:16竖屏。"

echo
echo "== 结果 =="
for sid in scene_hutong_shuxia scene_hutong_menkou scene_hutong_wunei; do
  ls -lh "$L/$sid/plate_1.jpg" 2>/dev/null | awk '{print "  "$5"  "$9}'
done
