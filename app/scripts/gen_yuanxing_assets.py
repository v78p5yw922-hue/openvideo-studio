#!/usr/bin/env python3
"""Generate 《远星矿脉》 assets from the layered image prompts (verbatim bodies).

Assets:
  char  Lin_Che  ref_1   <- prompt #1 正脸半身定妆 (main)
  char  Lin_Che  ref_2   <- prompt #2 近景锁脸     (second view)
  scene scene_cabin   plate_1 <- prompt #4 采样舱空镜母版
  scene scene_airlock plate_1 <- prompt #5 气闸舱空镜母版

H3 has no negative-prompt input, so the user's negative list cannot be passed
directly. Instead each prompt keeps its positive craft terms and we append a
positive style tail that encodes the same intent.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path.home() / "openvideo/video-agent"
LIB = REPO / "drama/library"
TMP = REPO / "segments/_assets"

STYLE_TAIL = ("，low-key practical lighting, soft falloff, negative fill, micro-scratches, "
              "compressed rubber seal, visible weave, faint condensation, 35mm film grain, "
              "slight halation, documentary-like industrial, used future, tactile, realistic。")

# 统一负面词（H3 无 negative 输入，这里仅记录，作为生成时的人工禁忌）
NEGATIVE = ("opaque helmet, dark visor, reflective visor covering face, faceless helmet, "
            "glossy plastic toy look, video game render, cartoon, anime, HDR, oversharpened, "
            "uniform flat lighting, clean empty surfaces, perfect symmetry, wide shot, "
            "full body shot, full ship wide shot, hologram interface, laser beam, explosion, "
            "energy shield, particle storm, text, watermark, real logo, real government insignia")

ASSETS = [
    ("char", "Lin_Che", "ref_1.png",
     "9:16 vertical, medium close-up chest-up portrait of Lin Che, 29-year-old Chinese deep-space "
     "sampling engineer, slight 3/4 angle, calm narrow eyes, thin scar over left brow, short black "
     "hair with a few stray strands, dry lips, visible skin pores and faint stubble, tired but "
     "focused expression. Dark grey abrasion-resistant woven jumpsuit with visible weave, amber "
     "identity strip slightly frayed at the edge, matte plastic shoulder buckle with mold seam, amber "
     "rubber-sealed gloves with knuckle creases, compact tool pouch. Clear helmet with fully "
     "transparent visor, face and eyes fully visible, faint breath condensation inside the visor edge, "
     "subtle fingerprints on the visor rim, matte plastic seal ring compressed against the neck. "
     "Background: blurred sampling cabin, brushed metal bench with directional grain, cold teal LED "
     "strip, rubber-sealed porthole, fine dust motes in the air. Lighting: low-key top cold white LED, "
     "small amber keypad pool from below on the chin, brief red warning sweep on the right wall, soft "
     "falloff, negative fill on the left. 85mm equivalent, shallow depth of field, slight halation, "
     "35mm film grain, documentary-like industrial, used future, tactile, realistic, subject fills "
     "65% of frame."),
    ("char", "Lin_Che", "ref_2.png",
     "9:16 vertical, close-up of Lin Che's face inside a clear helmet, face fills 75% of frame, fully "
     "transparent visor with no dark reflection, visible eyes, nose, mouth, thin scar over left brow, "
     "short black hair, faint breath condensation near the visor edge, tiny dust specks on the visor, "
     "skin texture with pores and dry lips, restrained breathing. Dark grey woven jumpsuit collar and "
     "frayed amber identity strip at bottom frame, matte plastic seal ring at frame edge. Lighting: "
     "cold teal reflection on the left cheek from a sample cabinet, small amber glow from below on the "
     "chin, brief red warning sweep crossing the right temple, low-key, soft falloff, negative fill. "
     "Background heavily blurred brushed metal and rubber seal. 85mm equivalent, shallow depth of "
     "field, slight halation, 35mm film grain, documentary-like industrial, tactile, realistic, "
     "vertical 9:16."),
    ("scene", "scene_cabin", "plate_1.jpg",
     "9:16 vertical, empty compact exo-planet sampling cabin, no people, camera behind the sample "
     "bench looking toward the bench and cabin wall, medium shot, subject fills 60% of frame. Brushed "
     "metal sample bench at center with visible directional grain and small clamp marks, matte plastic "
     "clamps with mold seams and worn edges, amber keypads with worn edges, cold teal LED strip sample "
     "cabinet on the left with a faint uneven glow, rubber-sealed porthole on the right with compressed "
     "gasket and tiny scratches on the glass, woven fabric ceiling panels with slight pilling, cable "
     "conduits with dust in the seams. Fine dust motes suspended in the air. Lighting: low-key top cold "
     "white LED strip down, cold teal spill from the left cabinet, small amber pool from the keypads "
     "below, brief red warning sweep across the right wall, soft falloff, negative fill. 35mm "
     "equivalent, shallow depth of field, slight halation, 35mm film grain, documentary-like "
     "industrial, used future, tactile, realistic, no HDR."),
    ("scene", "scene_airlock", "plate_1.jpg",
     "9:16 vertical, empty abandoned exo relay platform airlock, no people, camera inside the airlock "
     "looking toward the circular outer door, medium shot, subject fills 60% of frame. Circular outer "
     "door at center with brushed metal and micro-scratches, rubber seal permanently compressed and "
     "slightly aged, manual wheel at left-center with matte plastic grip worn smooth at contact points, "
     "matte plastic pressure gauge on the right with fine scratches on the glass and a needle slightly "
     "trembling, anti-slip floor grating with faint boot scuff marks, overhead LED strip with a small "
     "dead segment. Cold blue glow leaking through the narrowest part of the door seam, volumetric "
     "haze, no visible object. Lighting: low-key top cold white emergency LED, cold blue spill from the "
     "door seam, brief red warning sweep across the left wall, soft falloff, negative fill. 35mm "
     "equivalent, shallow depth of field, slight halation, 35mm film grain, documentary-like "
     "industrial, used future, tactile, realistic, no HDR."),
]


def run(cmd: list[str]) -> int:
    return subprocess.run(cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True).returncode


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    (TMP / "negative.txt").write_text(NEGATIVE, encoding="utf-8")
    ok = True
    for kind, ident, fname, prompt in ASSETS:
        if kind == "char":
            out = LIB / "characters" / ident / fname
        else:
            out = LIB / "scenes" / ident / fname
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size > 50_000:
            print(f"  {ident}/{fname}: exists")
            continue
        raw = TMP / f"{kind}_{ident}_{fname}.mp4"
        print(f"=== {ident}/{fname} ===")
        rc = run([".venv/bin/open-video", prompt + STYLE_TAIL, "--aspect", "9:16",
                  "--duration", "5", "--output", str(raw)])
        if rc != 0 or not raw.exists():
            print(f"  render FAILED rc={rc}")
            ok = False
            continue
        rc = run(["ffmpeg", "-y", "-v", "error", "-ss", "2.2", "-i", str(raw),
                  "-frames:v", "1", "-update", "1", "-q:v", "2", str(out)])
        print(f"  {'ok' if rc == 0 else 'frame FAILED'} -> {out.relative_to(REPO)}")
        ok &= rc == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
