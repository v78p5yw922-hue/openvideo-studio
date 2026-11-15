"""Export the cut as EDL (CMX3600) and OTIO JSON.

Both are interchange formats, not containers: they reference the take files
already on disk. This is the cheapest way to hand a cut to DaVinci / Premiere
without adopting a timeline engine inside the studio.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from ..core import service

FPS = 24.0


def _frames(duration_s: float, fps: float = FPS) -> int:
    return max(1, round(duration_s * fps))


def _tc(frames: int, fps: int = 24) -> str:
    h = frames // (fps * 3600)
    m = (frames // (fps * 60)) % 60
    s = (frames // fps) % 60
    f = frames % fps
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def _take_duration(path: str) -> float:
    p = Path(path)
    if p.is_file():
        cp = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=nw=1:nk=1", str(p)],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        try:
            return float(cp.stdout.strip())
        except ValueError:
            pass
    return 5.0


def approved_cut(project_id: str) -> list[dict]:
    """Ordered shots with an approved take; rejected/stale shots are reported."""
    shots = service.recompute_fingerprints(project_id)
    out = []
    for sh in shots:
        st = service.shot_state(project_id, sh)
        if st["state"] == "passed" and st["take"]:
            out.append({
                "shot_id": sh["shot_id"],
                "path": st["take"]["path"],
                "version": st["take"]["version"],
                "duration": round(_take_duration(st["take"]["path"]), 3),
            })
    return out


def build_edl(project_id: str) -> dict:
    project = service.get_project(project_id) or {"title": project_id}
    clips = approved_cut(project_id)
    lines = [f"TITLE: {project['title']}", "FCM: NON-DROP FRAME", ""]
    cursor = 0
    for i, c in enumerate(clips, start=1):
        dur = _frames(c["duration"])
        src_in, src_out = 0, dur
        rec_in, rec_out = cursor, cursor + dur
        name = f"{c['shot_id']}_v{c['version']}"
        lines.append(f"{i:03d}  AX       V     C        "
                     f"{_tc(src_in)} {_tc(src_out)} {_tc(rec_in)} {_tc(rec_out)}")
        lines.append(f"* FROM CLIP NAME: {name}")
        lines.append("")
        cursor = rec_out
    return {"edl": "\n".join(lines), "clips": len(clips), "frames": cursor,
            "duration_s": round(cursor / FPS, 3),
            "missing": _missing(project_id)}


def build_otio(project_id: str) -> dict:
    project = service.get_project(project_id) or {"title": project_id}
    clips = approved_cut(project_id)
    children = []
    cursor = 0
    for c in clips:
        dur = _frames(c["duration"])
        children.append({
            "OTIO_SCHEMA": "Clip.2",
            "name": f"{c['shot_id']}_v{c['version']}",
            "metadata": {"shot_id": c["shot_id"], "version": c["version"]},
            "source_range": {
                "OTIO_SCHEMA": "TimeRange.1",
                "start_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 0, "rate": FPS},
                "duration": {"OTIO_SCHEMA": "RationalTime.1", "value": dur, "rate": FPS},
            },
            "media_references": {
                "DEFAULT_MEDIA": {
                    "OTIO_SCHEMA": "ExternalReference.1",
                    "target_url": f"file://{c['path']}",
                    "available_range": {
                        "OTIO_SCHEMA": "TimeRange.1",
                        "start_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 0, "rate": FPS},
                        "duration": {"OTIO_SCHEMA": "RationalTime.1", "value": dur, "rate": FPS},
                    },
                },
            },
            "active_media_reference_key": "DEFAULT_MEDIA",
        })
        cursor += dur
    return {
        "OTIO_SCHEMA": "Timeline.1",
        "name": project["title"],
        "global_start_time": {"OTIO_SCHEMA": "RationalTime.1", "value": 0, "rate": FPS},
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": [{
                "OTIO_SCHEMA": "Track.1",
                "name": "V1",
                "kind": "Video",
                "children": children,
            }],
        },
        "metadata": {"project_id": project_id, "clip_count": len(children),
                     "frames": cursor},
    }


def _missing(project_id: str) -> list[dict]:
    shots = service.list_shots(project_id)
    out = []
    for sh in shots:
        st = service.shot_state(project_id, sh)
        if st["state"] != "passed":
            out.append({"shot_id": sh["shot_id"], "state": st["state"]})
    return out
