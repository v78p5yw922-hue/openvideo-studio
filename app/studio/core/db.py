"""Storage: one SQLite file, one writer per table, no second source of truth.

Facts  -> takes (renderer writes through service.record_take)
Decisions -> reviews
Work -> jobs
Audit -> events (append-only)
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("STUDIO_DATA", Path.home() / "openvideo/studio-data"))
DB_PATH = DATA_DIR / "studio.db"
SCHEMA = Path(__file__).with_name("schema.sql")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def txn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with txn() as conn:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before a column existed."""
    want = {
        "scenes": [("locked", "INTEGER NOT NULL DEFAULT 0")],
        "characters": [("locked", "INTEGER NOT NULL DEFAULT 0")],
        "takes": [("kind", "TEXT NOT NULL DEFAULT 'final'")],
        "shots": [("first_frame", "TEXT NOT NULL DEFAULT ''")],
    }
    for table, cols in want.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def loads(raw: str, default):
    try:
        return json.loads(raw) if raw else default
    except json.JSONDecodeError:
        return default


def emit(conn: sqlite3.Connection, project_id: str, shot_id: str, type_: str, payload: dict) -> None:
    conn.execute(
        "INSERT INTO events(project_id, shot_id, type, payload, created_at) VALUES (?,?,?,?,?)",
        (project_id, shot_id, type_, json.dumps(payload, ensure_ascii=False), now()),
    )
