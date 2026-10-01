#!/usr/bin/env python3
"""Build emulationstation2.mo (en_US) that relabels the Sinden RECOIL choices in ES's
Controllers menu. ES looks its English labels up through gettext (LANG=en_US.UTF-8), so an
en_US catalogue only needs the strings we want to change; everything else stays as written.
The patcher copies the result to /usr/share/locale/en_US/LC_MESSAGES/ at every boot.

    QUIET GUN          (gun-quiet)         -> AUTO - SOFT
    MACHINE GUN        (machinegun)        -> AUTO - MEDIUM
    QUIET MACHINE GUN  (machinegun-quiet)  -> AUTO - HARD
"""
import struct
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "/userdata/system/sinden-recoil/emulationstation2.mo"
MESSAGES = {
    "": "Content-Type: text/plain; charset=UTF-8\n",
    "QUIET GUN": "AUTO - SOFT",
    "MACHINE GUN": "AUTO - MEDIUM",
    "QUIET MACHINE GUN": "AUTO - HARD",
}

# GNU .mo layout: header, table of original strings, table of translations, then the strings.
keys = sorted(MESSAGES)                       # must be sorted for gettext's binary search
ids = b"".join(k.encode() + b"\0" for k in keys)
strs = b"".join(MESSAGES[k].encode() + b"\0" for k in keys)
n = len(keys)
key_start = 7 * 4 + 16 * n
koffsets, voffsets, o = [], [], 0
for k in keys:
    koffsets += [len(k.encode()), key_start + o]
    o += len(k.encode()) + 1
o = 0
for k in keys:
    v = MESSAGES[k].encode()
    voffsets += [len(v), key_start + len(ids) + o]
    o += len(v) + 1
data = struct.pack("Iiiiiii", 0x950412de, 0, n, 7 * 4, 7 * 4 + n * 8, 0, 0)
data += struct.pack(f"{2 * n}i", *koffsets) + struct.pack(f"{2 * n}i", *voffsets) + ids + strs
open(OUT, "wb").write(data)
print(f"wrote {OUT} ({n - 1} labels)")
