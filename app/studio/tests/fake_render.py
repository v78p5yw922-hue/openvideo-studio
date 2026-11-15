#!/usr/bin/env python3
"""Fake renderer for tests: behaves like render_drama.py, no GPU.

Writes one dummy .mp4 into --out and prints a peak line, so the executor's
parsing / take naming / fingerprint recording can be tested for real.
"""
import argparse
import re
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("script")
ap.add_argument("--out", required=True)
ap.add_argument("--only", required=True)
ap.add_argument("--width", default="768")
ap.add_argument("--height", default="1344")
ap.add_argument("--steps", default="20")
ap.add_argument("--no-upscale", action="store_true")
args, _ = ap.parse_known_args()

out = Path(args.out)
out.mkdir(parents=True, exist_ok=True)
nums = re.findall(r"\d+", args.only)
idx = int(nums[0]) if nums else 1
target = out / f"{idx:04d}.mp4"
target.write_bytes(b"\x00" * 4096)

print(f"[drama] [0{idx}] start")
for step in range(1, 5):
    print(f"[drama] [0{idx}] step {step}/4")
print(f"[drama] [0{idx}] OK 123.4s peak=23456 MiB -> {target.name}")
print("done")
sys.exit(0)
