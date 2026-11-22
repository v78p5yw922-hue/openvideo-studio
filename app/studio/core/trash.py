"""回收站：删除不物理删，先移到 studio-data/trash/<日期>/，之后统一清空。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from .. import config as _config
from . import db


def trash_dir(day: str | None = None) -> Path:
    day = day or time.strftime("%Y%m%d")
    d = db.DATA_DIR / "trash" / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def move_to_trash(path: str | Path, kind: str = "", owner: str = "") -> str:
    """把文件移到回收站，返回新路径；源文件不存在时返回空串。"""
    src = Path(path)
    if not src.is_file():
        return ""
    stem = src.stem
    suffix = src.suffix
    tag = "_".join(x for x in (kind, owner) if x) or "item"
    dst = trash_dir() / f"{tag}_{stem}{suffix}"
    i = 1
    while dst.exists():
        dst = trash_dir() / f"{tag}_{stem}_{i}{suffix}"
        i += 1
    shutil.move(str(src), str(dst))
    return str(dst)


def list_trash() -> list[dict]:
    root = db.DATA_DIR / "trash"
    if not root.is_dir():
        return []
    out = []
    for f in sorted(root.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.is_file():
            out.append({"path": str(f), "name": f.name, "size": f.stat().st_size,
                        "day": f.parent.name})
    return out


def empty_trash() -> dict:
    root = db.DATA_DIR / "trash"
    if not root.is_dir():
        return {"removed": 0}
    n = 0
    for f in list(root.rglob("*")):
        if f.is_file():
            f.unlink()
            n += 1
    for d in sorted(root.rglob("*"), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()
            except OSError:
                pass
    return {"removed": n}
