"""登录系统：密码哈希、会话、默认管理员、用户管理。"""

from __future__ import annotations

from studio.core import auth, db

db.init_db()
auth.ensure_default_admin()


def test_password_hash_roundtrip():
    stored = auth.hash_password("wsmm1234")
    assert stored.startswith("pbkdf2$")
    assert auth.verify_password("wsmm1234", stored)
    assert not auth.verify_password("wrong", stored)


def test_default_admin_exists():
    auth.ensure_default_admin()
    user = auth.get_by_username("admin")
    assert user is not None
    assert user["role"] == auth.ROLE_ADMIN
    assert auth.authenticate("admin", "wsmm1234") is not None
    assert auth.authenticate("admin", "bad") is None


def test_session_lifecycle():
    user = auth.get_by_username("admin")
    sess = auth.create_session(user["id"], days=365)
    resolved = auth.resolve_session(sess["token"])
    assert resolved and resolved["username"] == "admin"
    auth.delete_session(sess["token"])
    assert auth.resolve_session(sess["token"]) is None


def test_create_and_delete_user():
    u = auth.create_user("editor1", "secret123", "剪辑", "13800000000")
    assert u["display_name"] == "剪辑"
    assert auth.authenticate("editor1", "secret123") is not None
    assert auth.check_password(u["id"], "secret123")
    # 重名被拒
    try:
        auth.create_user("editor1", "secret123")
        raise AssertionError("重名不应通过")
    except ValueError:
        pass
    auth.update_user(u["id"], password="newpass123")
    assert auth.authenticate("editor1", "newpass123") is not None
    auth.delete_user(u["id"])
    assert auth.get_user(u["id"]) is None


def test_cannot_delete_last_admin():
    admins = [u for u in auth.list_users() if u["role"] == auth.ROLE_ADMIN]
    assert len(admins) >= 1
    last = admins[0]
    try:
        auth.delete_user(last["id"])
        raise AssertionError("应至少保留一个管理员")
    except ValueError:
        pass


def test_api_login_and_guard():
    """守卫默认开启：不带 token 访问受保护接口应 401。"""
    import os

    from fastapi.testclient import TestClient

    from studio.api import app as app_module

    os.environ["STUDIO_AUTH"] = "1"
    try:
        with TestClient(app_module.app) as client:
            assert client.get("/api/projects").status_code == 401
            bad = client.post("/api/auth/login",
                              json={"username": "admin", "password": "nope"})
            assert bad.status_code == 401
            ok = client.post("/api/auth/login",
                             json={"username": "admin", "password": "wsmm1234"})
            assert ok.status_code == 200
            token = ok.json()["token"]
            assert client.get("/api/projects",
                              headers={"Authorization": f"Bearer {token}"}).status_code == 200
            assert client.get("/api/auth/me",
                              headers={"Authorization": f"Bearer {token}"}).json()["user"]["username"] == "admin"
    finally:
        os.environ["STUDIO_AUTH"] = "0"
