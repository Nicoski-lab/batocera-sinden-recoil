#!/bin/bash
# Batocera game hook (/userdata/system/scripts/): set the Sinden recoil mode for the game
# that starts (games.conf: single / machinegun / default), back to default when it stops.
# Called by emulatorlauncher as: <gameStart|gameStop> <system> <emulator> <core> <rom>
CTL=/userdata/system/sinden-recoil/recoilctl.py

case "$1" in
    gameStart)
        python3 "$CTL" game "$2" "$5"
        # per-game helpers that watch the game's ammo (MAME games use Lua plugins instead)
        case "$2/$(basename "$5")" in
            naomi/hotd2.zip)
                setsid nohup python3 /userdata/system/sinden-recoil/hotd2/hotd2-sinden.py \
                    >/dev/null 2>&1 < /dev/null &
                ;;
        esac
        ;;
    gameStop)
        python3 "$CTL" stop                # recoil back on only once the triggers are released
        ;;
esac
exit 0
