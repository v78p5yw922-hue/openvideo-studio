#!/usr/bin/env bash
# 安全重启：队列里有任务时拒绝重启（渲染中被 systemctl restart 杀掉的任务，
# 错误信息是"服务重启，任务中断"——这已经浪费过一次 7 分钟的渲染）。
set -euo pipefail

API="${OV_API:-http://127.0.0.1:8801}"
FORCE="${1:-}"

active=$(curl -s --max-time 5 "$API/api/queue/global" \
         | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d.get("count",0))' 2>/dev/null || echo "?")

if [ "$active" = "?" ]; then
  echo "⚠  查不到队列状态（服务可能已停），继续重启"
elif [ "$active" != "0" ] && [ "$FORCE" != "--force" ]; then
  echo "✗ 队列里还有 $active 个任务在跑，拒绝重启。"
  echo "  等它跑完，或确认要放弃渲染：$0 --force"
  curl -s --max-time 5 "$API/api/queue/global" \
    | python3 -c 'import sys,json
d=json.load(sys.stdin)
for j in d.get("active",[]):
    p=json.loads(j.get("payload") or "{}")
    print(f"   - {j[\"id\"][:8]} {p.get(\"shots\")} {j[\"state\"]}")' 2>/dev/null || true
  exit 1
fi

echo "→ 重启 studio.service"
sudo systemctl restart studio.service
for i in $(seq 1 20); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$API/api/health" || true)
  if [ "$code" = "200" ]; then echo "✓ 服务已恢复"; exit 0; fi
  sleep 1
done
echo "✗ 服务 20 秒内未就绪，查看：journalctl -u studio.service -n 50"
exit 2
