#!/usr/bin/env python3
"""Locate Flycast's emulated main RAM inside the RetroArch process: take 3 signatures with
RetroArch's READ_CORE_RAM (slow, 1 request/frame) and search the process's writable
mappings for them via /proc/<pid>/mem (fast). Prints the host address of guest RAM offset 0."""
import re
import socket
import subprocess

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.settimeout(1)


def ra_read(addr, n):
    s.sendto(f"READ_CORE_RAM {addr:x} {n}\n".encode(), ("127.0.0.1", 55355))
    return bytes(int(x, 16) for x in s.recvfrom(65536)[0].decode().split()[2:])


pid = subprocess.run(["pgrep", "-x", "retroarch"], capture_output=True, text=True).stdout.split()[0]
sigs = []
for off in range(0x10000, 0x2000000, 0x1F3000):     # spread over the 32 MB
    b = ra_read(off, 64)
    if len(set(b)) > 8:                      # skip flat areas
        sigs.append((off, b))
    if len(sigs) >= 4:
        break
print("pid", pid, "signatures at", [hex(o) for o, _ in sigs])
maps = []
for l in open(f"/proc/{pid}/maps"):
    m = re.match(r"([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)", l)
    a, b = int(m.group(1), 16), int(m.group(2), 16)
    if "r" in m.group(3) and not m.group(4).startswith("[v"):
        maps.append((a, b, m.group(3), m.group(4)))
mem = open(f"/proc/{pid}/mem", "rb", 0)
for a, b, perm, name in maps:
    try:
        mem.seek(a)
        data = mem.read(b - a)
    except OSError:
        continue
    for off0, sig0 in sigs:
        i = data.find(sig0)
        while i != -1:
            print(f"sig {off0:#x} found at host 0x{a + i:x} -> base 0x{a + i - off0:x} "
                  f"(mapping {a:x}-{b:x} {perm} {name[-40:]} size {b - a:#x})")
            i = data.find(sig0, i + 1)
