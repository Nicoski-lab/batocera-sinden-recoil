#!/usr/bin/env python3
"""Print every button press/release of the Sinden input devices for N seconds (default 60).
Usage: gun-buttons-watch.py [seconds]"""
import select
import sys
import time
import evdev

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 60
devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
devs = [d for d in devs if "sinden" in d.name.lower()]
print("watching:", ", ".join(f"{d.path} ({d.name})" for d in devs), flush=True)
end = time.time() + secs
while time.time() < end:
    r, _, _ = select.select(devs, [], [], 0.5)
    for d in r:
        for e in d.read():
            if e.type == evdev.ecodes.EV_KEY and e.value in (0, 1):
                n = evdev.ecodes.BTN.get(e.code) or evdev.ecodes.KEY.get(e.code) or e.code
                state = "down" if e.value else "up"
                print(f"{time.strftime('%H:%M:%S')} {d.path} {d.name}: {n} {state}", flush=True)
print("done", flush=True)
