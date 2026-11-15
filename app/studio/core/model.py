"""Shot identity, input fingerprint, cascade invalidation.

Design decisions (from the three-way review):

* A shot's identity is `shot_id`, never its position. Files are named
  `<shot_id>_v<version>.mp4`; ordinal only decides concat order.
* Every shot carries a fingerprint of everything that can change its pixels:
  its own fields, its scene (including the plate's hash), its cast refs hashes,
  and render params. A take is valid only when its stored fingerprint matches.
* A `chain` shot folds in the *previous shot's fingerprint*, so re-rendering or
  editing s03 automatically invalidates s04..sN while the chain holds. That is
  the cascade, and it needs no manual bookkeeping.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

RENDER_PARAMS_DEFAULT = {
    "width": 1344,      # landscape (phone held sideways) — 2026-11-14 change
    "height": 768,
    "steps": 20,
    "seed": 42,
    "model": "minimax_h3",
}

STATES = ("empty", "queued", "running", "review", "passed", "rejected", "stale")


def sha256_file(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def shot_fingerprint(
    shot: dict,
    scene: dict | None,
    cast_refs: list[str],
    params: dict | None = None,
    prev_fingerprint: str = "",
) -> str:
    p = dict(RENDER_PARAMS_DEFAULT)
    if params:
        p.update(params)
    sc = scene or {}
    payload = {
        "shot": {
            "shot_id": shot.get("shot_id", ""),
            "scene_id": shot.get("scene_id", ""),
            "cast": sorted(shot.get("cast") or []),
            "camera": shot.get("camera", ""),
            "prompt": shot.get("prompt", ""),
            "dialogue": shot.get("dialogue", ""),
            "transition": shot.get("transition", "cut"),
            "anchor": shot.get("anchor", ""),
            "duration": round(float(shot.get("duration") or 5.0), 3),
        },
        "scene": {
            "scene_id": sc.get("scene_id", ""),
            "camera": sc.get("camera", ""),
            "props": sc.get("props", ""),
            "light": sc.get("light", ""),
            "plate_sha": sc.get("plate_sha", ""),
        },
        "cast_refs": sorted(cast_refs),
        "params": p,
        "chain_from": prev_fingerprint if shot.get("transition") == "chain" else "",
    }
    # only when actually pinned: adding the feature must not change hashes of
    # shots that never used it (that would invalidate every earlier take)
    if shot.get("first_frame"):
        payload["shot"]["first_frame"] = sha256_file(shot["first_frame"])
    return sha256_text(canonical(payload))


def cascade(shots: list[dict], changed_index: int, fp_fn) -> list[int]:
    """Return indices whose fingerprint must be recomputed after `changed_index`.

    Walks forward while the next shot is a `chain` of the previous one. Stops at
    the first `cut`, because a cut does not depend on its predecessor.
    """
    dirty = [changed_index]
    i = changed_index
    while i + 1 < len(shots) and str(shots[i + 1].get("transition")) == "chain":
        i += 1
        dirty.append(i)
    return dirty


def take_name(shot_id: str, version: int) -> str:
    return f"{shot_id}_v{version}.mp4"


def next_version(existing: list[int]) -> int:
    return (max(existing) + 1) if existing else 1
