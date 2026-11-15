#!/usr/bin/env python3
"""Back up studio-data: consistent SQLite snapshot + assets + takes index.

The database is the only non-regenerable data here; takes can be re-rendered but
cost hours of GPU time, so they are synced too (rsync, skip unchanged).

    python -m studio.backup [--dest /mnt/d/openvideo-backups/studio] [--keep 14]
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .core import db


def snapshot_sqlite(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)


def rsync(src: Path, dest: Path) -> str:
    dest.mkdir(parents=True, exist_ok=True)
    cp = subprocess.run(["rsync", "-a", "--delete", f"{src}/", f"{dest}/"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return "ok" if cp.returncode == 0 else (cp.stdout or "")[-200:]


def prune(root: Path, keep: int) -> list[str]:
    snaps = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    removed = []
    for old in snaps[keep:]:
        subprocess.run(["rm", "-rf", str(old)], check=False)
        removed.append(old.name)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(Path.home() / "openvideo/backups/studio"))
    ap.add_argument("--keep", type=int, default=14)
    args = ap.parse_args()

    dest_root = Path(args.dest)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    snap = dest_root / stamp
    snap.mkdir(parents=True, exist_ok=True)

    dbp = db.DB_PATH
    if not dbp.is_file():
        print(f"找不到数据库 {dbp}（STUDIO_DATA 指错？）", file=sys.stderr)
        return 2
    snapshot_sqlite(dbp, snap / "studio.db")
    result = {
        "stamp": stamp,
        "dest": str(snap),
        "db_bytes": (snap / "studio.db").stat().st_size if (snap / "studio.db").is_file() else 0,
        "projects": rsync(db.DATA_DIR / "projects", snap / "projects")
        if (db.DATA_DIR / "projects").is_dir() else "skip",
        "characters": rsync(db.DATA_DIR / "characters", snap / "characters")
        if (db.DATA_DIR / "characters").is_dir() else "skip",
    }
    result["pruned"] = prune(dest_root, args.keep)
    print(f"backup {stamp} -> {snap}")
    print(f"  db: {result['db_bytes']} bytes")
    print(f"  projects: {result['projects']} | characters: {result['characters']}")
    if result["pruned"]:
        print(f"  pruned: {', '.join(result['pruned'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
