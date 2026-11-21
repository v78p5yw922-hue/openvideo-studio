"""账号与会话：本地多用户登录。

设计取舍：
* 密码用 PBKDF2-SHA256 哈希（stdlib，无第三方依赖），格式 `pbkdf2$轮数$盐$摘要`。
* 会话是服务端 token（存在 `sessions` 表），不是 JWT —— 退出登录能立刻失效。
* 首次启动自动创建管理员 `admin` / `wsmm1234`；只在 users 表为空时创建。
* 长期登录：token 默认 365 天有效，每次访问刷新 last_seen。
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time

from . import db

PBKDF2_ROUNDS = 200_000
SESSION_DAYS = 365
DEFAULT_ADMIN = ("admin", "wsmm1234")

ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"


# ------------------------------------------------------------------ 密码

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
    except ValueError:
        return False
    if algo != "pbkdf2":
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt), int(rounds))
    return hmac.compare_digest(calc.hex(), digest)


# ------------------------------------------------------------------ 用户

def _row_user(row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"] or row["username"],
        "phone": row["phone"],
        "role": row["role"],
        "active": bool(row["active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def ensure_default_admin() -> None:
    """users 表为空时创建默认管理员（只建一次，之后不会覆盖密码）。"""
    with db.txn() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if n:
            return
        username, password = DEFAULT_ADMIN
        ts = db.now()
        conn.execute(
            "INSERT INTO users(id, username, display_name, phone, role, password_hash,"
            " active, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (secrets.token_hex(8), username, username, "", ROLE_ADMIN,
             hash_password(password), ts, ts),
        )


def list_users() -> list[dict]:
    with db.txn() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
    return [_row_user(r) for r in rows]


def get_user(uid: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return _row_user(row) if row else None


def get_by_username(username: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _row_user(row) if row else None


def create_user(username: str, password: str, display_name: str = "",
                phone: str = "", role: str = ROLE_MEMBER) -> dict:
    username = (username or "").strip()
    if not username:
        raise ValueError("用户名不能为空")
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    if role not in (ROLE_ADMIN, ROLE_MEMBER):
        raise ValueError("角色只能是 admin 或 member")
    ts = db.now()
    uid = secrets.token_hex(8)
    with db.txn() as conn:
        exists = conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        if exists:
            raise ValueError("用户名已存在")
        conn.execute(
            "INSERT INTO users(id, username, display_name, phone, role, password_hash,"
            " active, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
            (uid, username, display_name or username, phone, role,
             hash_password(password), ts, ts),
        )
    return get_user(uid)  # type: ignore[return-value]


def update_user(uid: str, *, display_name: str | None = None, phone: str | None = None,
                role: str | None = None, active: bool | None = None,
                password: str | None = None) -> dict:
    sets: list[str] = []
    vals: list[object] = []
    if display_name is not None:
        sets.append("display_name = ?")
        vals.append(display_name)
    if phone is not None:
        sets.append("phone = ?")
        vals.append(phone)
    if role is not None:
        if role not in (ROLE_ADMIN, ROLE_MEMBER):
            raise ValueError("角色只能是 admin 或 member")
        sets.append("role = ?")
        vals.append(role)
    if active is not None:
        sets.append("active = ?")
        vals.append(1 if active else 0)
    if password:
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        sets.append("password_hash = ?")
        vals.append(hash_password(password))
    if not sets:
        raise ValueError("没有需要更新的字段")
    sets.append("updated_at = ?")
    vals.append(db.now())
    vals.append(uid)
    with db.txn() as conn:
        cur = conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", vals)
        if cur.rowcount == 0:
            raise ValueError("用户不存在")
    return get_user(uid)  # type: ignore[return-value]


def delete_user(uid: str) -> None:
    with db.txn() as conn:
        row = conn.execute("SELECT id, role FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if row["role"] == ROLE_ADMIN:
            admins = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = ? AND active = 1", (ROLE_ADMIN,)
            ).fetchone()["c"]
            if admins <= 1:
                raise ValueError("至少要保留一个管理员")
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))


# ------------------------------------------------------------------ 会话

def check_password(uid: str, password: str) -> bool:
    """校验某个用户当前密码（改密码时用）。"""
    with db.txn() as conn:
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()
    return bool(row) and verify_password(password, row["password_hash"])


def authenticate(username: str, password: str) -> dict | None:
    with db.txn() as conn:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not row or not row["active"]:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return _row_user(row)


def create_session(user_id: str, days: int = SESSION_DAYS) -> dict:
    token = secrets.token_urlsafe(32)
    ts = db.now()
    expires = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + days * 86400))
    with db.txn() as conn:
        conn.execute(
            "INSERT INTO sessions(token, user_id, created_at, expires_at, last_seen)"
            " VALUES (?,?,?,?,?)", (token, user_id, ts, expires, ts),
        )
    return {"token": token, "expires_at": expires}


def resolve_session(token: str) -> dict | None:
    if not token:
        return None
    with db.txn() as conn:
        row = conn.execute(
            "SELECT s.token, s.expires_at, u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token = ?", (token,),
        ).fetchone()
        if not row:
            return None
        if row["expires_at"] <= db.now():
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None
        if not row["active"]:
            return None
        conn.execute("UPDATE sessions SET last_seen = ? WHERE token = ?", (db.now(), token))
    return _row_user(row)


def delete_session(token: str) -> None:
    if not token:
        return
    with db.txn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
