"""ComfyClient 的测试：用本地假引擎，不需要 GPU。"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from studio.exec.comfy import ComfyClient


class _Handler(BaseHTTPRequestHandler):
    prompts: dict = {}
    deleted: bool = False

    def log_message(self, *a):  # 静音
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/prompt":
            wf = payload.get("prompt") or {}
            if "bad" in wf:
                return self._json({"prompt_id": "x", "node_errors": {"1": "nope"}})
            pid = f"p{len(self.prompts) + 1}"
            self.prompts[pid] = "ok"
            return self._json({"prompt_id": pid})
        if self.path == "/free" or self.path == "/interrupt":
            return self._json({})
        return self._json({}, 404)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/history/"):
            pid = self.path.rsplit("/", 1)[-1]
            if pid in self.prompts:
                return self._json({pid: {"status": {"status_str": "success"},
                                         "outputs": {"9": {"images": [
                                             {"filename": "a.mp4", "subfolder": "", "type": "output"}]}}}})
            return self._json({})
        if self.path == "/queue":
            return self._json({"queue_running": [], "queue_pending": []})
        if self.path.startswith("/view"):
            body = b"x" * 4096
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return self._json({}, 404)


@pytest.fixture()
def server():
    _Handler.prompts = {}
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_submit_ok(server):
    c = ComfyClient(server)
    ok, pid = c.submit({"1": {"class_type": "X", "inputs": {}}})
    assert ok and pid == "p1"


def test_submit_rejected(server):
    c = ComfyClient(server)
    ok, err = c.submit({"bad": {"class_type": "X", "inputs": {}}})
    assert not ok and "rejected" in err


def test_wait_success(server):
    c = ComfyClient(server)
    ok, pid = c.submit({"1": {"class_type": "X", "inputs": {}}})
    done, info = c.wait(pid, timeout=5, beat=99, poll=0.05)
    assert done
    assert json.loads(info)["prompt_id"] == pid


def test_wait_detects_external_delete(server):
    c = ComfyClient(server)
    done, err = c.wait("ghost", timeout=5, beat=99, poll=0.02,
                       missing_after=0.05, missing_grace=0.05)
    assert not done
    assert "外部删除" in err


def test_download_writes_file(server, tmp_path):
    c = ComfyClient(server)
    info = json.dumps({"outputs": {"9": {"images": [
        {"filename": "a.mp4", "subfolder": "", "type": "output"}]}}})
    dst = tmp_path / "out.mp4"
    assert c.download(info, dst)
    assert dst.stat().st_size > 1024
