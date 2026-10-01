# batocera-sinden-recoil

Game-aware recoil for **Sinden light guns on Batocera** (tested on Batocera 43.1, LightgunMono 1.8,
two guns, MAME 0.285 standalone, Flycast libretro). Made on a home arcade cabinet; shared because a
Batocera developer asked how the House of the Dead setup works.

What it adds on top of Batocera's stock Sinden support:

| Feature | How |
|---|---|
| **Per-game recoil mode** (single / machine gun / mixed) at **AUTO soft / medium / hard** strength, picked in the normal ES Sinden menu | live serial commands to the gun at game start (no tracker restart) + an en_US gettext catalogue that relabels three existing ES menu choices |
| **No kick on an empty clip** (House of the Dead 1 & 2) | the game's ammo counter is watched every frame; empty clip -> kick strength 0 |
| **Rack-to-reload** in HOTD 1 (MAME) | Sinden rack = `BTN_RIGHT` = MAME `GUNCODE_n_BUTTON2`, turned into a 4-frame off-screen shot |
| **No white gun flash** in HOTD 1 (MAME) | one-instruction ROM patch (community patch, credits below) |
| **No solenoid rattle on exit** (Batocera's gun exit combo holds the trigger 2 s) | combo detected in-game + recoil re-enabled only after the triggers are released |

Everything lives in `/userdata`, so it survives Batocera updates.

---

## 1. Talking to the gun while LightgunMono runs

Batocera's tracker (`LightgunMono.exe` under mono, one per gun) sends the recoil setup to the gun
**once**, when the gun is plugged in; from then on the **gun firmware** kicks by itself on every
trigger pull. The firmware takes fixed 7-byte frames on the gun's CDC-ACM port
(`/dev/ttyACM*`, 115200):

```
AA <cmd> p1 p2 p3 p4 BB
```

Decoded from `monodis LightgunMono.exe` (and matching github.com/schlarpc/sindenrs):

| cmd | meaning | params |
|---|---|---|
| `A1` (161) | recoil enable | p1 = 0/1 |
| `A2` (162) | auto-recoil params | strength, start delay, strength, delay between pulses |
| `A3` (163) | trigger mode | p1 = 0 single, 1 repeat while held |
| `A4` (164) | which events kick | trigger, trigger off-screen, pump on, pump off |
| `A7` (167) / `AC` (172) | single-shot strength | **these reply bytes** - avoid while the tracker runs |
| `A8` (168) | one kick now | - |

A 7-byte `write()` to a tty is atomic, so these frames can be sent **while the tracker keeps
running** (it writes its own position frames to the same port) - no restart, no tracking gap.
Only send commands that don't reply: the tracker's `MoveMouse` loop reads the port
(`0xFE` = button packet, `200` = ack) and stray reply bytes would land in it.

Guns are identified by USB product id (`16c0:0f39` / `16c0:0f01` here), never by tty number.

### Firmware pitfall (important)

**Don't mute a gun with `A1 0` / `A1 1`.** After re-enabling, the firmware swallows the next
trigger pull, and if the trigger was released while recoil was off it keeps "holding" it and fires
slow repeats on its own (we saw it during cutscenes). Instead keep `A1` on and resend the current
mode's `A2` frame with **strength 0** (empty clip) or the normal strength (loaded). `recoilctl.py`
writes the active mode's A2 parameters to `/var/run/sinden-recoil.a2` for the helpers.

"Single" mode at a chosen strength: true single (`A3 0`) kicks at the single-shot strength
(167/172, which we can't set safely), so with an AUTO level single is emulated as repeat mode with
the longest start/pulse delay (`A2 s 255 s 255`): one kick per pull at the auto strength.

## 2. Per-game modes (`recoil/`)

* `recoilctl.py` - `mode default|single|machinegun|off`, `game <system> <rom>`, `stop`, `kick`, `status`.
* `sinden-recoil.sh` - Batocera user game hook (`/userdata/system/scripts/`): `gameStart` ->
  `recoilctl.py game $2 $5` (+ starts per-game helpers), `gameStop` -> `recoilctl.py stop`.
* `games.conf` - `<system> <rom file> <mode>`; built from ES's Lightgun collection by
  `make-games-conf.py` (machine-gun games, pistol games; mixed-weapon games stay on default).
* `stop` applies the menu settings with recoil off and re-enables it from a detached process once
  no `Sinden lightgun` evdev device reports `BTN_LEFT` (exit combo = trigger + rack + front-left).

**AUTO levels in the ES menu without patching ES:** the RECOIL choices are compiled into ES, but
their labels go through gettext with `LANG=en_US.UTF-8`. `make-es-mo.py` builds a tiny
`emulationstation2.mo` for `/usr/share/locale/en_US/LC_MESSAGES/` that renames
`QUIET GUN / MACHINE GUN / QUIET MACHINE GUN` -> `AUTO - SOFT / MEDIUM / HARD`; the values stay
`gun-quiet / machinegun / machinegun-quiet`, `recoilctl.py` maps them to strengths 40 / 70 / 100,
and a patch to `/usr/bin/virtual-sindenlightgun-add` makes the tracker's base config match.
(`/usr` is an overlay reset at boot: `extras/patch-gun-borders.py` re-applies all of this from a
`/userdata` service at every boot - it also contains our other, unrelated Sinden tweaks.)

## 3. House of the Dead (MAME `hotdo`, 2 guns) - `games/hotd-mame/`

Why MAME instead of model2emu: under Wine all mice merge, so both Sinden guns were P1. MAME
standalone with `-lightgunprovider udev` gives each gun its own player, and Lua makes it scriptable.
The plugin is loaded per ROM with `/userdata/system/configs/mame/hotdo.ini`
(`autoboot_script ...`; Batocera's MAME `-inipath` includes that folder).

* **No flash:** `regions[":maincpu"]:write_u32(0x18610, 0x0a000000)` (i960 `ret`) when the dword is
  still `0x5ca01e00` - the "no flash screen" patch by **egregiousguy**, found in model2emu's
  `scripts/hotd.lua` (`Romset_PatchDWord(ROMBASE,0x18610,0xa000000)`; ROMBASE is 0 in MAME too).
  Note: forcing the game's white-frame flag (workram+0x430b0) to 0 also hides the flash but
  **breaks P1 shot registration** - the shot is processed in that frame.
* **Ammo:** workram (i960 0x500000) `+0x1ec41` P1, `+0x1ed0d` P2, 6..0 (matches model2emu's cheat).
* **Rack to reload:** `manager.machine.input:seq_pressed(seq_from_tokens("GUNCODE_1_BUTTON2"))`
  -> `P1_X` field `set_value(minvalue)` + `P1 Trigger` `set_value(1)` for 4 frames, then
  `clear_value()` (same trick as MAME's `offscreenreload` plugin, without per-game setup). The
  synthetic trigger never reaches the gun, so a rack doesn't kick. The kick is restored at the
  rack itself (+1 s grace): the game refills the clip a few frames later.
* **Exit combo:** `GUNCODE_n_BUTTON2` + `GUNCODE_n_BUTTON3` held -> mute that gun.
* `make-hotd-bezel.py` - no arcade HOTD bezel exists (the real cabinet bezel was plain black);
  builds one from The Bezel Project's Saturn HOTD art (console parts removed) + the scraped logo.

## 4. House of the Dead 2 (Naomi, Flycast libretro) - `games/hotd2-flycast/`

MAME's Naomi driver runs it at ~37 % and glitchy, so it stays on Flycast; racking already reloads
and there is no flash, only the empty-clip kick needed fixing.

* RetroArch's `READ_CORE_MEMORY` says "no memory map" for Flycast; `READ_CORE_RAM` works but
  answers one request per frame (1 MB = 17 s). Instead: Flycast's 32 MB guest RAM is a shared
  mapping of the RetroArch process (`/dev/shm/dcnzorz_mem`, `rw-s`, size `0x2000000`), readable
  through `/proc/<pid>/mem` at GB/s (`tools/find-ram.py` locates it from a few `READ_CORE_RAM`
  signatures).
* Ammo: guest `0x3d0444` P1, `0x3d0544` P2 (6..0).
* `hotd2-sinden.py` is started detached by the game hook, polls at 60 Hz, reads rack /
  exit-combo state from the virtual gun evdev devices (matched by product id) and exits with
  RetroArch.

## 5. Finding the addresses (`tools/`)

* `hotd-recorder.lua` - MAME: RAM snapshot of all shares on every trigger edge (+3 frames later);
  `HOTD_AUTO=1` plays by itself (coins, both starts, both guns fire at the centre - HOTD wants 2
  credits per player, coin only after boot), `HOTD_FLASH=1` per-frame screenshots + RAM around
  shots, `HOTD_NOFLASH=<hex>` write-tap test. `find-ammo.py` / `find-flash.py` analyse them.
* `record-series.py` - Flycast, **natural play**: logs every trigger/rack event, takes the bytes
  equal to 6 right after the first rack, samples them at 20 Hz and ranks them against a simulated
  clip (shot -1, rack = 6). Asking a player for an exact shot sequence doesn't work in HOTD - you die.
* `hotd-autopilot-test.lua` - end-to-end test of the MAME plugin without a player.

## Install (as used on the cabinet)

```
/userdata/system/sinden-recoil/{recoilctl.py,games.conf,make-es-mo.py,...}
/userdata/system/sinden-recoil/hotd/hotd-sinden.lua
/userdata/system/sinden-recoil/hotd2/hotd2-sinden.py
/userdata/system/scripts/sinden-recoil.sh          (chmod +x, LF line endings!)
/userdata/system/configs/mame/hotdo.ini
```

A CRLF line ending in a `/userdata/system/scripts/` hook makes `#!/bin/bash\r` unexecutable and
Batocera then aborts **every** game launch - keep LF.

## Credits

* Sinden serial protocol cross-checked with [schlarpc/sindenrs](https://github.com/schlarpc/sindenrs).
* HOTD "no flash screen" patch: egregiousguy (via model2emu's `hotd.lua`).
* Bezel art source: [The Bezel Project](https://github.com/thebezelproject).

MIT licensed (code only; no ROM, BIOS or artwork is included).
