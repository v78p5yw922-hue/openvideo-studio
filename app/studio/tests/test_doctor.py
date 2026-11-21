"""doctor 的单元测试：不依赖真实引擎。"""

from __future__ import annotations

from studio import doctor


def test_report_shape(monkeypatch):
    """report() 必须有固定结构；引擎连不上时是 fail 但不是异常。"""
    monkeypatch.setattr(doctor, "check_engine",
                        lambda: (doctor._check("engine", "ComfyUI 在线", "fail", "连不上"), None))
    monkeypatch.setattr(doctor, "check_nodes",
                        lambda: doctor._check("nodes", "节点齐全", "fail", "缺 H3CastToVideo（comfy-kitchen）"))
    rep = doctor.report()
    assert set(rep) >= {"ok", "summary", "fail", "warn", "checks", "data_dir", "content_dir"}
    assert rep["ok"] is False
    assert rep["fail"] >= 2
    assert any(c["key"] == "engine" for c in rep["checks"])


def test_all_ok(monkeypatch):
    ok = lambda key, label, detail="": doctor._check(key, label, "ok", detail)  # noqa: E731
    monkeypatch.setattr(doctor, "check_engine", lambda: (ok("engine", "ComfyUI 在线"), None))
    monkeypatch.setattr(doctor, "check_nodes", lambda: ok("nodes", "节点齐全"))
    monkeypatch.setattr(doctor, "check_models", lambda: ok("models", "权重文件"))
    monkeypatch.setattr(doctor, "check_ffmpeg", lambda: ok("ffmpeg", "ffmpeg 可用"))
    monkeypatch.setattr(doctor, "check_gpu", lambda: ok("gpu", "GPU 可见"))
    rep = doctor.report()
    assert rep["ok"] is True
    assert "可以开始" in rep["summary"]


def test_storage_writable():
    c = doctor.check_storage()
    assert c["status"] in ("ok", "warn")


def test_missing_models_is_fail(monkeypatch, tmp_path):
    real_modules = __import__("studio.config", fromlist=["DEFAULTS"]).DEFAULTS["models"]

    def fake_get(key, default=None):
        if key == "comfy.root":
            return str(tmp_path)
        if key.startswith("models."):
            return real_modules.get(key.split(".", 1)[1], "")
        return default

    monkeypatch.setattr(doctor.config, "get", fake_get)
    c = doctor.check_models()
    assert c["status"] == "fail"
    assert "缺" in c["detail"]
