#!/usr/bin/env python3
"""Change the Sinden guns' recoil live, while the tracker (LightgunMono) keeps running.

The tracker sends the recoil setup to the gun once, when the gun is plugged in; after that
the gun's firmware kicks by itself on every trigger pull. The firmware takes 7-byte frames
`AA <cmd> p1 p2 p3 p4 BB` on the gun's serial port (protocol: LightgunMono 1.8 disassembly,
matches github.com/schlarpc/sindenrs). We only send frames that get no reply, so nothing
ends up in the tracker's read buffer:
    161 A1  recoil on/off            163 A3  trigger mode: 0 single, 1 repeat while held
    162 A2  auto params: strength, start delay, strength, delay between pulses
    164 A4  events: trigger, trigger offscreen, pump on, pump off
    168 A8  one kick now
A tty write() of 7 bytes is atomic, so our frames never interleave with the tracker's.

Menu: Controllers > guns > RECOIL = AUTO - SOFT / MEDIUM / HARD (Batocera values gun-quiet /
machinegun / machinegun-quiet, relabelled) -> per-game modes at that strength; GUN / DISABLED ->
untouched. Single-shot strength is still the tracker's own (cmd 167/172 reply bytes into the
tracker's read loop, so they are not sent here).

Usage:
    recoilctl.py mode <default|single|machinegun|off> [p1|p2|all]
    recoilctl.py kick [p1|p2|all]
    recoilctl.py stop                          # game exit: menu mode, recoil on once triggers are up
    recoilctl.py game <system> <rom path>     # apply the mode listed in games.conf
    recoilctl.py status
"""
import glob
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
GAMES = os.path.join(HERE, "games.conf")
STATE = "/var/run/sinden-recoil.mode"
A2_STATE = "/var/run/sinden-recoil.a2"   # "strength start strength pulse" of the current mode
LOG = "/userdata/system/logs/sinden-recoil.log"
PLAYERS = {"p1": "0f39", "p2": "0f01"}   # fixed: P1 = red 0f39, P2 = blue 0f01 (never swap)
CONF = "/userdata/system/batocera.conf"
# ES menu Controllers > guns > RECOIL. Three of Batocera's values are relabelled AUTO - SOFT /
# MEDIUM / HARD (en_US translation installed by the patcher, which also makes the tracker use
# these strengths in mixed mode). Only these turn per-game auto modes on; "gun" (classic)
# and "disabled" leave the guns exactly as Batocera set them.
LEVELS = {"gun-quiet": ("soft", 40), "machinegun": ("medium", 70), "machinegun-quiet": ("hard", 100)}


def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def gun_ports():
    """{"p1": "/dev/ttyACM0", ...} from the USB product id of each ttyACM."""
    ports = {}
    for tty in sorted(glob.glob("/sys/class/tty/ttyACM*")):
        usb = os.path.realpath(os.path.join(tty, "device", ".."))
        try:
            vid = open(os.path.join(usb, "idVendor")).read().strip()
            pid = open(os.path.join(usb, "idProduct")).read().strip()
        except OSError:
            continue
        for player, want in PLAYERS.items():
            if vid == "16c0" and pid == want:
                ports[player] = "/dev/" + os.path.basename(tty)
    return ports


def tracker_config(port):
    """Recoil values Batocera gave the tracker driving this port (= the default mode)."""
    for cfg in glob.glob("/var/run/sinden/p*/LightgunMono-*.exe.config"):
        txt = open(cfg).read()
        if f'key="SerialPortWrite" value="{port}"' not in txt:
            continue
        val = lambda k, d: int(m.group(1)) if (m := re.search(f'key="{k}"\\s*value="(\\d+)"', txt)) else d
        return {"enable": val("EnableRecoil", 1), "repeat": val("TriggerRecoilNormalOrRepeat", 1),
                "strength": val("AutoRecoilStrength", 40), "start": val("AutoRecoilStartDelay", 150),
                "pulse": val("AutoRecoilDelayBetweenPulses", 10)}
    return {"enable": 1, "repeat": 1, "strength": 40, "start": 150, "pulse": 10}


def recoil_setting():
    try:
        for l in open(CONF):
            if l.startswith("controllers.guns.recoil="):
                return l.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def frame(cmd, a=0, b=0, c=0, d=0):
    return bytes([0xAA, cmd, a & 0xFF, b & 0xFF, c & 0xFF, d & 0xFF, 0xBB])


def send(port, frames):
    fd = os.open(port, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
    try:
        for f in frames:
            os.write(fd, f)
            time.sleep(0.02)
    finally:
        os.close(fd)


def mode_frames(mode, cfg):
    s, pulse = cfg["strength"], cfg["pulse"]
    if mode == "off":
        return [frame(0xA1, 0)]
    if mode == "default":
        return [frame(0xA2, s, cfg["start"], s, pulse), frame(0xA3, cfg["repeat"]),
                frame(0xA1, cfg["enable"])]
    if mode == "single":
        if cfg.get("level"):
            # AUTO level: true single (A3=0) kicks at the gun's single-shot strength (cmd 167/172,
            # tracker sets 80 = loud), which we can't set safely yet. Use repeat mode instead -
            # its kicks use the auto strength - with the longest start/pulse delay: one kick per
            # pull, slow repeats only if the trigger is held past ~0.25 s.
            return [frame(0xA2, s, 255, s, 255), frame(0xA3, 1), frame(0xA1, cfg["enable"])]
        return [frame(0xA3, 0), frame(0xA1, cfg["enable"])]   # one kick per pull, never repeats
    if mode == "machinegun":    # kicks start at once and repeat while the trigger is held
        return [frame(0xA2, s, 1, s, pulse), frame(0xA3, 1), frame(0xA1, cfg["enable"])]
    raise SystemExit(f"unknown mode {mode}")


def targets(who):
    ports = gun_ports()
    return ports if who == "all" else {who: ports[who]} if who in ports else {}


def current_mode():
    try:
        return open(STATE).read().strip()
    except OSError:
        return "default"                  # nothing sent since boot/replug = Batocera's setting


def set_mode(mode, who="all"):
    level = LEVELS.get(recoil_setting())
    tag = f"{mode}:{level[0]}" if level else mode
    if mode == "default" and who == "all" and current_mode() in ("default", tag):
        return                            # every non-gun game launch: nothing to do
    for player, port in targets(who).items():
        cfg = tracker_config(port)
        if level:                         # AUTO level: same strength in every mode, mixed = 150 ms
            cfg.update(strength=level[1], start=150, repeat=1, level=level[0])
        if not cfg["enable"] and mode != "off":
            continue                      # recoil switched off in Batocera: leave it off
        try:
            frames = mode_frames(mode, cfg)
            send(port, frames)
            log(f"{player} {port}: {mode}" + (f" ({level[0]}, strength {level[1]})" if level else ""))
            # per-game ammo helpers (hotd Lua plugin, hotd2-sinden.py) mute an empty clip by
            # resending this A2 frame with strength 0 - NOT with A1 off: re-enabling A1 makes the
            # firmware swallow the next pull or keep repeating a trigger it saw pressed while off
            a2 = next((f for f in frames if f[1] == 0xA2), None)
            if a2:
                open(A2_STATE, "w").write(" ".join(str(b) for b in a2[2:6]) + "\n")
            elif os.path.exists(A2_STATE):
                os.remove(A2_STATE)
        except OSError as e:
            log(f"{player} {port}: {mode} FAILED ({e})")
    try:
        open(STATE, "w").write(tag + "\n")
    except OSError:
        pass


def triggers_held():
    """True while any Sinden virtual gun reports its trigger (BTN_LEFT) down."""
    try:
        import evdev
        for path in evdev.list_devices():
            d = evdev.InputDevice(path)
            if d.name == "Sinden lightgun" and evdev.ecodes.BTN_LEFT in d.active_keys():
                return True
    except Exception:
        pass
    return False


def stop():
    """Game exit: menu settings, but recoil OFF until every trigger is released - Batocera's
    gun exit combo holds the trigger 2 s, which would otherwise rattle the solenoid in the
    menu's repeat mode. The re-enable runs detached so ES isn't held up."""
    level = LEVELS.get(recoil_setting())
    for player, port in targets("all").items():
        cfg = tracker_config(port)
        if level:
            cfg.update(strength=level[1], start=150, repeat=1, level=level[0])
        frames = [frame(0xA1, 0)] + mode_frames("default", cfg)[:-1]   # all but the final enable
        try:
            send(port, frames)
        except OSError as e:
            log(f"{player} {port}: stop FAILED ({e})")
    try:
        open(STATE, "w").write((f"default:{level[0]}" if level else "default") + "\n")
    except OSError:
        pass
    if os.fork() == 0:                    # detached waiter
        os.setsid()
        t = time.time()
        while triggers_held() and time.time() - t < 20:
            time.sleep(0.1)
        for player, port in targets("all").items():
            try:
                send(port, [frame(0xA1, tracker_config(port)["enable"])])
            except OSError:
                pass
        log(f"stop: recoil back on after trigger release ({time.time() - t:.1f} s)")
        os._exit(0)


def game_mode(system, rom):
    """Mode for a game from games.conf: `<system> <rom file name> <mode>` (tab/space
    separated, rom may contain spaces; `*` as rom = whole system). Default: default."""
    name = os.path.basename(rom.rstrip("/"))
    found = "default"
    try:
        lines = open(GAMES).read().splitlines()
    except OSError:
        return found
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        sysname, rest = line.split(None, 1)
        game, mode = rest.rsplit(None, 1)
        if sysname == system and game == "*":
            found = mode
        elif sysname == system and game == name:
            return mode
    return found


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    cmd = argv[1]
    if cmd == "mode":
        set_mode(argv[2], argv[3] if len(argv) > 3 else "all")
    elif cmd == "stop":
        stop()
    elif cmd == "kick":
        for player, port in targets(argv[2] if len(argv) > 2 else "all").items():
            send(port, [frame(0xA8)])
    elif cmd == "game":
        setting = recoil_setting()
        if setting not in LEVELS:         # classic GUN / DISABLED: hands off
            return
        mode = game_mode(argv[2], argv[3])
        log(f"game {argv[2]} {os.path.basename(argv[3])} -> {mode}")
        set_mode(mode)
    elif cmd == "status":
        for player, port in gun_ports().items():
            print(player, port, tracker_config(port))
        print("menu setting:", recoil_setting(), "->", LEVELS.get(recoil_setting(), ("classic, no auto",))[0])
        print("mode:", open(STATE).read().strip() if os.path.exists(STATE) else "default (untouched)")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
