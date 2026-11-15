#!/usr/bin/env python3
"""Fake still renderer: writes a tiny JPEG-ish file, no GPU."""
import argparse
import sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--prompt", required=True)
ap.add_argument("--out", required=True)
ap.add_argument("--duration", default="3")
ap.add_argument("--seed", default="42")
ap.add_argument("--width", default="480")
ap.add_argument("--height", default="840")
args, _ = ap.parse_known_args()

out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 2048 + b"\xff\xd9")
print('{"ok": true}')
sys.exit(0)
