"""ComfyUI 传输适配层。

目的：把"怎么跟引擎说话"从"怎么组工作流"里拆出来。
现在只有 ComfyClient 一个实现；将来换后端（另一个 HTTP 引擎、远程服务、队列代理），
只要实现同样的五个方法：submit / wait / free / object_info / download。

行为与改造前完全一致（返回签名、日志文案、超时策略都保留），
所以这是一次**纯搬家**，不改变渲染结果。
"""

from __future__ import annotations

import json
import shutil
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


class ComfyClient:
    """一个 ComfyUI 服务的极简客户端。所有方法都不抛异常给调用方（除 get/post 内部）。"""

    def __init__(self, server: str, client_id: str = "open-video-drama") -> None:
        self.server = server.rstrip("/")
        self.client_id = client_id

    # ---------------------------------------------------------------- 基础 HTTP
    def post(self, path: str, payload: dict, timeout: float = 30) -> dict:
        req = urllib.request.Request(self.server + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def get(self, path: str, timeout: float = 15) -> dict:
        with urllib.request.urlopen(self.server + path, timeout=timeout) as r:
            return json.loads(r.read())

    # ---------------------------------------------------------------- 提交
    def submit(self, wf: dict) -> tuple[bool, str]:
        """返回 (成功, prompt_id | 错误说明)。"""
        try:
            r = self.post("/prompt", {"prompt": wf, "client_id": self.client_id})
        except Exception as e:  # noqa: BLE001
            return False, f"submit failed: {e}"
        if r.get("node_errors"):
            return False, f"rejected: {str(r['node_errors'])[:400]}"
        pid = r.get("prompt_id")
        if not pid:
            return False, f"submit failed: 没有 prompt_id（{str(r)[:200]}）"
        return True, pid

    # ---------------------------------------------------------------- 等待
    def wait(self, pid: str, timeout: float,
             log: Callable[[str], None] = print,
             beat: float = 20.0, poll: float = 3.0,
             missing_after: float = 25.0, missing_grace: float = 12.0) -> tuple[bool, str]:
        """等一个 prompt 落地。返回 (成功, outputs_json | 错误说明)。

        · 每 `beat` 秒打一次心跳（长镜单步 1–2 分钟，不打上层会误判卡死）
        · 25 秒后开始核对 /queue：prompt 若被外部删除（浏览器开着 ComfyUI 页面常见），
          12 秒内确认不在队列也不在历史，就立刻失败，不空等。
        """
        t0 = time.time()
        missing_since: float | None = None
        last_beat = 0.0
        while time.time() - t0 < timeout:
            if time.time() - last_beat >= beat:
                last_beat = time.time()
                log(f"[wait] 引擎在跑，已等 {int(last_beat - t0)} 秒（长镜单步较慢，属正常）")
            try:
                h = self.get(f"/history/{pid}")
            except Exception:  # noqa: BLE001
                h = {}
            if pid in h:
                st = h[pid].get("status", {})
                if st.get("status_str") == "success":
                    return True, json.dumps({"prompt_id": pid,
                                             "outputs": h[pid].get("outputs", {})})
                return False, f"status={st.get('status_str')}"

            elapsed = time.time() - t0
            if elapsed > missing_after:
                try:
                    q = self.get("/queue")
                    ids = {item[1] for item in (q.get("queue_running") or [])}
                    ids |= {item[1] for item in (q.get("queue_pending") or [])}
                    alive = pid in ids
                except Exception:  # noqa: BLE001
                    alive = True
                if alive:
                    missing_since = None
                else:
                    missing_since = missing_since or time.time()
                    if time.time() - missing_since > missing_grace:
                        return False, "prompt 被外部删除（队列与历史都没有）"
            time.sleep(poll)
        return False, "timeout"

    # ---------------------------------------------------------------- 显存
    def free(self, wait: float = 1.5, log: Callable[[str], None] = print) -> None:
        log("[free] 清理显存")
        try:
            self.post("/free", {"unload_models": True, "free_memory": True})
            time.sleep(wait)
        except Exception:  # noqa: BLE001  清理失败不该阻断渲染
            pass

    def interrupt(self) -> None:
        try:
            self.post("/interrupt", {})
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- 其他
    def object_info(self, timeout: float = 30) -> dict:
        return self.get("/object_info", timeout=timeout)

    def system_stats(self, timeout: float = 5) -> dict:
        return self.get("/system_stats", timeout=timeout)

    def download(self, info_json: str, dst: Path, keys=("gifs", "videos", "images")) -> bool:
        """把 history 里第一个可下载产物存到 dst。"""
        outs = (json.loads(info_json or "{}") or {}).get("outputs", {})
        for payload in outs.values():
            for key in keys:
                for g in payload.get(key) or []:
                    q = urllib.parse.urlencode({"filename": g["filename"],
                                                "subfolder": g.get("subfolder", ""),
                                                "type": g.get("type", "output")})
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    with urllib.request.urlopen(f"{self.server}/view?{q}", timeout=180) as r, \
                            dst.open("wb") as fh:
                        shutil.copyfileobj(r, fh)
                    if dst.stat().st_size > 1024:
                        return True
        return False
