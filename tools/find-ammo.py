#!/usr/bin/env python3
"""Find per-player ammo counters in hotd-recorder dumps: for each player's consecutive shots,
the byte (or 16/32-bit word) that goes N, N-1, ... 1, 0 between the "before" and "after"
snapshot of each shot, then stays at 0 on empty shots."""
import glob
import os
import re
import numpy as np

D = "/userdata/system/hotd-rec/"
shares = []
for l in open(D + "info.txt"):
    m = re.match(r"share (\S+) size (\d+) offset_in_dump (\d+)", l)
    if m:
        shares.append((m.group(1), int(m.group(2)), int(m.group(3))))


def where(off):
    for tag, size, base in shares:
        if base <= off < base + size:
            return f"{tag}+0x{off - base:x}"
    return f"?{off}"


shots = {}
for f in sorted(glob.glob(D + "*_p?_*.bin")):
    if f.endswith("_after.bin"):
        continue
    n, p, fr = re.match(r".*/(\d+)_p(\d)_(\d+)\.bin", f).groups()
    shots.setdefault(p, []).append((int(n), int(fr), f, f[:-4] + "_after.bin"))

for p, lst in sorted(shots.items()):
    lst = [s for s in lst if os.path.exists(s[3])]
    print(f"== P{p}: {len(lst)} shots")
    before = np.stack([np.fromfile(s[2], dtype=np.uint8) for s in lst])
    after = np.stack([np.fromfile(s[3], dtype=np.uint8) for s in lst])
    for width, dt in ((1, np.uint8), (2, "<u2"), (4, "<u4")):
        b = before.view(dt).astype(np.int64) if width > 1 else before.astype(np.int64)
        a = after.view(dt).astype(np.int64) if width > 1 else after.astype(np.int64)
        dec = (a == b - 1)                     # this shot used one bullet
        small = (b >= 0) & (b <= 12)
        # best run of consecutive decrementing shots per offset
        run = np.zeros(b.shape[1], dtype=np.int64); best = np.zeros_like(run)
        for i in range(b.shape[0]):
            run = np.where(dec[i] & small[i], run + 1, 0)
            best = np.maximum(best, run)
        cand = np.argsort(-best)[:8]
        for c in cand:
            if best[c] < 4:
                break
            seq = b[:, c][:24].tolist()
            print(f"  w{width} {where(c * width)} run {best[c]}  before-values {seq}")
