"""Build games.conf for recoilctl.py from the ES Lightgun collection (guns.json).
Titles not listed here stay on "default" (Batocera's gun-quiet: tap = 1 kick, hold = repeat
after 0.15 s), which suits mixed-weapon games (Time Crisis 3/4, Virtua Cop, Overkill, ...)."""
import json, os

HERE = os.path.dirname(os.path.abspath(__file__))
MACHINEGUN = [  # holding the trigger fires continuously
    "Alien 3 : The Gun", "Gunbuster",
    "House of the Dead 4", "Let's Go Jungle! :  Lost on the Island of Spice", "ZZZ(notgame):Let's Go Jungle", "Rambo",
    "Behind Enemy Lines (2 guns - MAME)", "Behind... Enemy Lines", "Gunblade NY", "Rail Chase 2",
    "L.A. Machineguns", "Star Wars Trilogy Arcade", "The Lost World: Jurassic Park", "The Ocean Hunter",
    "Time Crisis : Crisis Zone", "Deadstorm Pirates", "Time Crisis: Razing Storm",
    "Gunblade NY & LA Machineguns: Arcade Hits Pack",
    "Heavy Fire : Black Arms", "Heavy Fire : Special Operations", "Heavy Fire: Afghanistan",
]
SINGLE = [  # pistols / rifles / shotguns: one kick per pull
    # 3DO + Singe FMV shooters
    "Corpse Killer", "Crime Patrol", "Demolition Man", "Drug Wars", "Mad Dog II : The Lost Gold",
    "Mad Dog McCree", "Space Pirates", "The Last Bounty Hunter", "Who Shot Johnny Rock",
    "Crime Patrol 2: Drug Wars", "Fast Draw Showdown V1.31", "Gallagher's Gallery V2.2",
    "Mad Dog II: The Lost Gold", "The Last Bounty Hunter v1.01", "Who Shot Johnny Rock?",
    # Atomiswave hunting/sport
    "Extreme Hunting", "Extreme Hunting 2", "Ranger Mission", "Sega Clay Challenge", "Sports Shooting USA",
    # Sega / Namco pistol arcade
    "The House of the Dead 2", "The House of the Dead", "The House of the Dead (2 guns - MAME)",
    "The House of the Dead EX (Japan)", "Bang!", "Point Blank", "Carnevil",
    "Confidential Mission", "Lupin The 3rd : The Shooting", "Ninja Assault",
    # NES Zapper (can't auto-fire)
    "Barker Bill's Trick Shooting", "Duck Hunt", "Freedom Force", "Gotcha! : The Sport!", "Gumshoe",
    "Hogan's Alley", "Mechanized Attack", "Operation Wolf", "Shooting Range", "To the Earth", "Wild Gunman",
    # SNES pistol-style
    "Bazooka Blitzkrieg", "Lethal Enforcers", "Tin Star", "Yoshi's Safari",
    # PS2 / PS3 pistol
    "Gunfighter II : Revenge of Jesse James", "Gunvari Collection + Time Crisis", "Time Crisis II",
    "Vampire Night", "The House of the Dead III", "Gal Gun",
    # Saturn
    # Wii pistol / rifle / shotgun
    "Arcade Shooting Gallery", "Big Buck Hunter Pro", "Fast Draw Showdown", "Gunslingers",
    "Mad Dog McCree : Gunslinger Pack", "Pheasants Forever : Wingshooter",
    "Remington Great American Bird Hunt", "Remington Super Slam Hunting -  Alaska",
    "Remington Super Slam Hunting : Africa", "Remington Super Slam Hunting : North America",
    "Wild West Guns", "Wild West Shootout",
]
games = json.load(open(os.path.join(HERE, "guns.json"), encoding="utf-8"))
names = {n for _, _, n in games}
for lst, label in ((MACHINEGUN, "machinegun"), (SINGLE, "single")):
    for t in lst:
        if t not in names:
            print(f"!! {label} title not found: {t}")
lines = ["# Sinden recoil mode per gun game, read by recoilctl.py at game start (scripts/sinden-recoil.sh).",
         "# <system> <rom file name> <mode>   modes: single | machinegun | default | off",
         "# Games not listed use default = Batocera's setting (gun-quiet: tap = 1 kick, hold = repeat",
         "# after 0.15 s) - kept for mixed-weapon games (Time Crisis 3/4, Virtua Cop, Overkill, RE ...).", ""]
counts = {"machinegun": 0, "single": 0, "default": 0}
for sysname, rom, name in sorted(games):
    mode = "machinegun" if name in MACHINEGUN else "single" if name in SINGLE else "default"
    counts[mode] += 1
    if mode == "default":
        lines.append(f"# {sysname} {rom} default   ({name})")
    else:
        lines.append(f"{sysname} {rom} {mode}   # {name}")
# not (yet) in the Lightgun collection: gamesdb id mismatch, fixed by the patcher (gamesdb-guns)
lines.append("wii House of the Dead 2 & 3 Return, The (USA) (En,Fr,Es).rvz single   # The House of the Dead 2 & 3 Return")
counts["single"] += 1
# the mode is the LAST word before any comment: recoilctl strips '#...' first
open(os.path.join(HERE, "games.conf"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
print(counts)
print("default (mixed/unsure):", sorted(n for _, _, n in games if n not in MACHINEGUN and n not in SINGLE))
