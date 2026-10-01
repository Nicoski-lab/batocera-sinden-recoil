#!/usr/bin/env python3
"""Ammo finder for natural play (Flycast HotD2). Logs every trigger pull / rack of one gun with
its time; at the first rack (clip full) takes the bytes equal to FULL as candidates, then samples
them 20x/s. At the end, simulates the clip from the event log (shot: -1 if >0, rack: FULL) and
ranks candidates by how often their value matches. Usage: record-series.py [p1|p2] [seconds]"""
import re
import select
import subprocess
import sys
import time
import evdev
import numpy as np

player = sys.argv[1] if len(sys.argv) > 1 else "p1"
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 75
FULL = 6
pid = subprocess.run(["pgrep", "-x", "retroarch"], capture_output=True, text=True).stdout.split()[0]
base = None
for l in open(f"/proc/{pid}/maps"):
    m = re.match(r"([0-9a-f]+)-([0-9a-f]+) rw-s .*dcnzorz_mem", l)
    if m and int(m.group(2), 16) - int(m.group(1), 16) == 0x2000000:
        base = int(m.group(1), 16)
mem = open(f"/proc/{pid}/mem", "rb", 0)


def ram():
    mem.seek(base)
    return np.frombuffer(mem.read(0x2000000), dtype=np.uint8)


gun = evdev.InputDevice("/dev/input/" + {"p1": "event27", "p2": "event30"}[player])
events, samples, times = [], [], []
cand, cand_at = None, None
end = time.time() + secs
next_sample = 0
print("recording - play normally", flush=True)
while time.time() < end:
    r, _, _ = select.select([gun], [], [], 0.01)
    if r:
        for e in gun.read():
            if e.type == evdev.ecodes.EV_KEY and e.value == 1 and e.code in (evdev.ecodes.BTN_LEFT, evdev.ecodes.BTN_RIGHT):
                kind = "shot" if e.code == evdev.ecodes.BTN_LEFT else "rack"
                events.append((time.time(), kind))
                if kind == "rack" and cand is None:
                    cand_at = time.time() + 0.4          # take candidates once the reload happened
    now = time.time()
    if cand is None and cand_at and now >= cand_at:
        cand = np.nonzero(ram() == FULL)[0]
        print(f"candidates: {len(cand)} bytes == {FULL}", flush=True)
    if cand is not None and now >= next_sample:
        samples.append(ram()[cand].copy())
        times.append(now)
        next_sample = now + 0.05
if cand is None:
    raise SystemExit("no rack seen - nothing to analyse")
S = np.stack(samples)                                    # samples x candidates
print(f"{len(events)} events, {S.shape[0]} samples", flush=True)
# expected clip at each sample, from events up to 0.15 s before it (game latency)
ev = [e for e in events if e[0] >= cand_at - 0.4]
exp, ammo, j = [], FULL, 0
for t in times:
    while j < len(ev) and ev[j][0] <= t - 0.15:
        ammo = FULL if ev[j][1] == "rack" else max(ammo - 1, 0)
        j += 1
    exp.append(ammo)
exp = np.array(exp)
score = (S == exp[:, None]).mean(axis=0)
changes = (np.diff(S.astype(np.int16), axis=0) != 0).sum(axis=0)
order = np.argsort(-score)[:10]
print("expected clip timeline:", "".join(str(x) for x in exp[::4]))
for k in order:
    print(f"  guest {cand[k]:#09x}: match {score[k]:.0%}, {changes[k]} changes, "
          f"timeline {''.join(str(min(v, 9)) for v in S[::4, k])}", flush=True)
