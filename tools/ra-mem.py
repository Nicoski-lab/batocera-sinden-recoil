#!/usr/bin/env python3
"""RetroArch network-command memory tool (READ_CORE_MEMORY over UDP 55355).
  ra-mem.py probe                     find which address range the core exposes
  ra-mem.py dump <hexstart> <len> out.bin
  ra-mem.py hunt <hexstart> <len>     ammo hunt: snapshot now (full clip, P1), then on every P1
                                      trigger pull re-read and keep bytes that went down by 1
"""
import socket
import sys
import time

ADDR = ("127.0.0.1", 55355)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(0.5)
CHUNK = 1024


def read(addr, n):
    sock.sendto(f"READ_CORE_MEMORY {addr:x} {n}\n".encode(), ADDR)
    data, _ = sock.recvfrom(65536)
    parts = data.decode().split()
    if len(parts) < 3 or parts[2] == "-1":
        raise IOError(data.decode().strip())
    return bytes(int(x, 16) for x in parts[2:])


def dump(start, length):
    out = bytearray()
    for a in range(start, start + length, CHUNK):
        out += read(a, min(CHUNK, start + length - a))
    return bytes(out)


def probe():
    for base in (0x0C000000, 0x8C000000, 0xAC000000, 0x00000000, 0x80000000):
        try:
            b = read(base, 16)
            print(f"{base:08x}: OK {b.hex()}")
        except Exception as e:
            print(f"{base:08x}: {e}")
    t = time.time()
    try:
        dump(0x0C000000, 1024 * 1024)
        print(f"1 MB in {time.time() - t:.2f} s")
    except Exception as e:
        print("speed test failed:", e)


def p1_trigger_presses():
    import evdev
    devs = [evdev.InputDevice(p) for p in evdev.list_devices()]
    gun = next(d for d in devs if d.name == "Sinden lightgun" and d.path.endswith("event27"))
    for e in gun.read_loop():
        if e.type == evdev.ecodes.EV_KEY and e.code == evdev.ecodes.BTN_LEFT and e.value == 1:
            yield


def hunt(start, length):
    snap = dump(start, length)
    cand = {i: v for i, v in enumerate(snap) if 1 <= v <= 12}
    print(f"snapshot taken: {len(cand)} candidates (bytes 1..12). Fire ONE shot every ~3 s.", flush=True)
    for n, _ in enumerate(p1_trigger_presses(), 1):
        time.sleep(0.4)
        new = dump(start, length)
        cand = {i: new[i] for i, v in cand.items() if new[i] == v - 1 or (v == 0 and new[i] == 0)}
        print(f"shot {n}: {len(cand)} candidates left", flush=True)
        if len(cand) <= 12:
            for i, v in sorted(cand.items()):
                print(f"   {start + i:08x} = {v}", flush=True)
        if len(cand) <= 3 and n >= 4:
            break


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "probe":
        probe()
    elif cmd == "dump":
        open(sys.argv[4], "wb").write(dump(int(sys.argv[2], 16), int(sys.argv[3])))
    elif cmd == "hunt":
        hunt(int(sys.argv[2], 16), int(sys.argv[3]))
