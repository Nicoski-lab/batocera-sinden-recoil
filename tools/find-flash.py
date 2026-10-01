#!/usr/bin/env python3
"""Pair each per-frame screenshot with its memory snapshot (same order), mark the white
"gun flash" frames by brightness, then list memory bytes whose value tells flash and
non-flash frames apart (same value on every flash frame, never on a normal frame)."""
import glob
import os
import re
import numpy as np
from PIL import Image

D = "/userdata/system/hotd-rec/"
snaps = sorted(glob.glob("/tmp/ht/snap/hotdo/*.png"), key=lambda f: int(os.path.basename(f)[:-4]))
dumps = sorted(glob.glob(D + "flash_*.bin"))
shares = [(m.group(1), int(m.group(2)), int(m.group(3))) for m in
          (re.match(r"share (\S+) size (\d+) offset_in_dump (\d+)", l) for l in open(D + "info.txt")) if m]


def where(off):
    for tag, size, base in shares:
        if base <= off < base + size:
            return f"{tag}+0x{off - base:x}"


rows = []
for s, d in zip(snaps, dumps):
    img = np.asarray(Image.open(s).convert("L"), dtype=np.float32)
    shot, f = re.search(r"flash_s(\d+)_f(\d+)", d).groups()
    rows.append((int(shot), int(f), img.mean(), img.std(), d))
flash = np.array([r[2] > 170 and r[3] < 40 for r in rows])
for shot in sorted({r[0] for r in rows}):
    line = " ".join(f"{r[2]:5.0f}{'*' if fl else ' '}" for r, fl in zip(rows, flash) if r[0] == shot)
    print(f"shot {shot:2d}: {line}")
print(f"{flash.sum()} flash frames of {len(rows)}")
if flash.any() and (~flash).any():
    mem = np.stack([np.fromfile(r[4], dtype=np.uint8) for r in rows])
    fv = mem[flash]
    same = (fv == fv[0]).all(axis=0)                       # constant on all flash frames
    never = ~(mem[~flash] == fv[0]).any(axis=0)            # that value never on normal frames
    hits = np.nonzero(same & never)[0]
    print(len(hits), "separating bytes")
    for h in hits[:40]:
        print(f"  {where(h)}  flash={fv[0][h]}  normal values={sorted(set(mem[~flash][:, h].tolist()))[:8]}")
