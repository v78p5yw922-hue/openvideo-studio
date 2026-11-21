#!/usr/bin/env python3
"""Create an in-scene character reference (character standing in the airlock).

Why: Cast reference images with a plain studio backdrop win over scene_images —
the model renders the character *as in the photo*, grey background included, and
only later drifts back to the scene. A reference shot inside the actual set
removes the grey backdrop entirely.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "openvideo/video-agent"
PLATE = REPO / "drama/library/scenes/scene_airlock/plate_1.jpg"
OUT = REPO / "drama/library/characters/Lin_Che/ref_airlock.png"
TMP = REPO / "segments/_assets"

PROMPT = (
    "9:16 vertical, medium close-up of Lin Che, 29-year-old Chinese deep-space sampling "
    "engineer, standing at the manual wheel of an abandoned airlock, right hand in an amber "
    "rubber-sealed glove resting on the wheel, head turned slightly toward the door seam, calm "
    "narrow eyes, thin scar over left brow, short black hair, clear helmet with fully transparent "
    "visor, face fully visible, faint breath condensation inside the visor, dark grey "
    "abrasion-resistant woven jumpsuit with frayed amber identity strip and matte plastic "
    "shoulder buckle. Background: circular outer door with brushed metal and compressed rubber "
    "seal, matte plastic pressure gauge, anti-slip floor grating, overhead emergency LED with one "
    "dead segment, cold blue glow leaking from the door seam, volumetric haze. "
    "Lighting: low-key top cold white, cold blue spill from the seam on the right side of his "
    "face, brief red warning sweep on the left wall, soft falloff, negative fill. "
    "85mm equivalent, shallow depth of field, slight halation, 35mm film grain, "
    "documentary-like industrial, used future, tactile, realistic, subject fills 70% of frame."
)


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    raw = TMP / "linche_airlock.mp4"
    print(f"== rendering in-scene ref from plate: {PLATE.name}")
    rc = subprocess.run(
        [".venv/bin/open-video", PROMPT, "--aspect", "9:16", "--duration", "5",
         "--mode", "i2v", "--first-frame", str(PLATE), "--output", str(raw)],
        cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if rc.returncode != 0 or not raw.exists():
        print(f"  render FAILED rc={rc.returncode}")
        print("  ---- cli output ----")
        for line in (rc.stdout or "").splitlines()[-12:]:
            print("   ", line)
        return 1
    rc = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2.2", "-i", str(raw),
                         "-frames:v", "1", "-update", "1", "-q:v", "2", str(OUT)],
                        cwd=str(REPO)).returncode
    print(f"  {'ok' if rc == 0 else 'frame FAILED'} -> {OUT.relative_to(REPO)}")

    # put the in-scene ref first so it wins over the studio backdrop
    sp = REPO / "drama/yuanxing_script.json"
    s = json.loads(sp.read_text(encoding="utf-8"))
    refs = [str(OUT.relative_to(REPO))]
    for p in sorted((OUT.parent).glob("ref_*.png")):
        r = str(p.relative_to(REPO))
        if r not in refs:
            refs.append(r)
    s["characters"][0]["reference_images"] = refs
    sp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  refs order:")
    for r in refs:
        print("   ", r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
