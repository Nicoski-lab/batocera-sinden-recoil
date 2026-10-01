#!/usr/bin/env python3
"""House of the Dead 2 (Naomi, Flycast libretro): no recoil on an empty clip.
Started in the background by /userdata/system/scripts/sinden-recoil.sh when the game starts;
exits by itself when RetroArch closes. Log: /userdata/system/logs/hotd2-sinden.log

* Flycast keeps the guest's 32 MB main RAM in a shared mapping of RetroArch
  (/dev/shm/dcnzorz_mem, rw-s, size 0x2000000) -> read straight from /proc/<pid>/mem.
* Ammo bytes (found by logging shots/racks during real play and simulating the clip):
  P1 guest 0x3d0444, P2 0x3d0544 (6..0). At 0 that gun's kick strength goes to 0, back to the
  mode's strength when the clip refills: the A2 frame of the current mode (saved by recoilctl in
  /var/run/sinden-recoil.a2) resent with strength 0/N. NOT recoil off/on (A1): after A1 0 -> 1 the
  firmware swallowed the next pull and, if the trigger was released while off, kept firing slow
  repeats on its own (seen in cutscenes).
* Racking already reloads in this game (Batocera's mapping); recoil is switched on at the rack
  itself so the first shot after a rack kicks, with a 1 s grace before 'empty' can turn it off.
* Exit combo (trigger + rack + front-left held): that gun's recoil off while rack + front-left
  are both down; sinden-recoil.sh's gameStop re-enables after the triggers are released.
"""
import glob
import os
import re
import select
import subprocess
import time

import evdev

AMMO = {"p1": 0x3d0444, "p2": 0x3d0544}
PRODUCT = {"p1": "0f39", "p2": "0f01"}          # P1 red, P2 blue: never swap
GRACE = 1.0
LOG = "/userdata/system/logs/hotd2-sinden.log"


def log(s):
    with open(LOG, "a") as f:
        f.write(time.strftime("%H:%M:%S ") + s + "\n")


def read(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def gun_ports():
    ports = {}
    for tty in glob.glob("/sys/class/tty/ttyACM*"):
        usb = os.path.realpath(tty + "/device/..")
        for player, pid in PRODUCT.items():
            if read(usb + "/idVendor") == "16c0" and read(usb + "/idProduct") == pid:
                ports[player] = "/dev/" + os.path.basename(tty)
    return ports


def gun_inputs():
    devs = {}
    for ev in glob.glob("/sys/class/input/event*"):
        if read(ev + "/device/name") == "Sinden lightgun":
            for player, pid in PRODUCT.items():
                if read(ev + "/device/id/product") == pid:
                    devs[player] = evdev.InputDevice("/dev/input/" + os.path.basename(ev))
    return devs


def recoil_disabled():
    try:
        return any(l.strip() == "controllers.guns.recoil=disabled" for l in open("/userdata/system/batocera.conf"))
    except OSError:
        return False


def find_ram(timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        for pid in subprocess.run(["pgrep", "-x", "retroarch"], capture_output=True, text=True).stdout.split():
            try:
                for l in open(f"/proc/{pid}/maps"):
                    m = re.match(r"([0-9a-f]+)-([0-9a-f]+) rw-s .*dcnzorz_mem", l)
                    if m and int(m.group(2), 16) - int(m.group(1), 16) == 0x2000000:
                        return int(pid), int(m.group(1), 16)
            except OSError:
                pass
        time.sleep(0.5)
    return None, None


def a2_params():
    try:
        return [int(x) for x in open("/var/run/sinden-recoil.a2").read().split()]
    except (OSError, ValueError):
        return None


def main():
    if recoil_disabled():
        log("recoil disabled in the menu: nothing to do"); return
    pid, base = find_ram()
    if not pid:
        log("Flycast RAM not found, giving up"); return
    ports, inputs = gun_ports(), gun_inputs()
    log(f"start: retroarch {pid}, RAM {base:#x}, guns {ports}, inputs {sorted(inputs)}")
    fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    sent, grace, combo = {}, {}, {}
    a2 = a2_params()
    log(f"mode A2 params {a2}" if a2 else "no A2 params (classic mode): falling back to A1 on/off")

    def set_recoil(player, on, why):
        if sent.get(player) == on or player not in ports:
            return
        sent[player] = on
        if a2:
            s = a2[0] if on else 0
            frame = bytes([0xAA, 0xA2, s, a2[1], s, a2[3], 0xBB])
        else:
            frame = bytes([0xAA, 0xA1, 1 if on else 0, 0, 0, 0, 0xBB])
        try:
            f = os.open(ports[player], os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            os.write(f, frame)
            os.close(f)
            log(f"{player} kick {'on' if on else 'muted'} ({why})")
        except OSError as e:
            log(f"{player} write failed: {e}")

    by_fd = {d.fd: (p, d) for p, d in inputs.items()}
    while True:
        r, _, _ = select.select(list(by_fd), [], [], 1 / 60)
        now = time.time()
        for f in r:
            player, dev = by_fd[f]
            try:
                events = list(dev.read())
            except OSError:
                continue
            for e in events:
                if e.type == evdev.ecodes.EV_KEY and e.code == evdev.ecodes.BTN_RIGHT and e.value == 1:
                    keys = dev.active_keys()
                    if evdev.ecodes.BTN_MIDDLE not in keys:
                        grace[player] = now + GRACE
                        set_recoil(player, True, "rack")
            keys = dev.active_keys()
            c = evdev.ecodes.BTN_RIGHT in keys and evdev.ecodes.BTN_MIDDLE in keys
            if c and not combo.get(player):
                set_recoil(player, False, "exit combo")
            if combo.get(player) and not c:
                sent.pop(player, None)              # re-evaluate from the ammo
            combo[player] = c
        try:
            for player, off in AMMO.items():
                loaded = os.pread(fd, 1, base + off)[0] > 0
                if combo.get(player):
                    continue
                if loaded or now >= grace.get(player, 0):
                    set_recoil(player, loaded, "clip loaded" if loaded else "clip empty")
        except OSError:
            log("RetroArch closed: exit"); return


if __name__ == "__main__":
    main()
