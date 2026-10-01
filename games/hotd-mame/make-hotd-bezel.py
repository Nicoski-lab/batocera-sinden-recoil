#!/usr/bin/env python3
"""Custom arcade bezel for House of the Dead (MAME hotdo). No real one exists: the original
cabinet's bezel was plain black, so no bezel pack has it. Built from art on the cabinet:
  * side panels = the gothic HotD texture of The Bezel Project's Saturn HotD art, with the
    Saturn console parts (POWER/RESET buttons, SEGA SATURN logo, grey TV frame) removed:
    the lower panel rows are rebuilt by mirroring the texture above them;
  * a black arcade-monitor frame (like the real cabinet) around a 4:3 opening;
  * the scraped HotD marquee logo, split: title on the left panel, the zombie on the right.
Usage: make-hotd-bezel.py [out.png]   (writes out.png + out.info)"""
import json
import sys
from PIL import Image, ImageDraw, ImageFilter

SAT = "/userdata/decorations/thebezelproject/games/saturn/House of the Dead, The (Europe) (Track 01).png"
LOGO = "/userdata/roms/model2/images/hotd-marquee.png"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/userdata/decorations/thebezelproject/games/model2/hotdo.png"
W, H = 1920, 1080
L, R, T, B = 250, 250, 6, 6             # opening = x 250..1670, y 6..1074 (1420x1068, 4:3)
PANEL = 222                             # width of the textured side panels in the Saturn art
CLEAN_TO = 770                          # Saturn buttons/logos start below this row

sat = Image.open(SAT).convert("RGBA")
canvas = Image.new("RGBA", (W, H), (0, 0, 0, 255))


def panel(box):
    """Side panel: texture rows 0..CLEAN_TO, then mirrored copies down to the bottom."""
    src = sat.crop((box[0], 0, box[1], CLEAN_TO))
    p = Image.new("RGBA", (box[1] - box[0], H))
    p.paste(src, (0, 0))
    y = CLEAN_TO
    flip = True
    while y < H:
        piece = src.transpose(Image.FLIP_TOP_BOTTOM) if flip else src
        p.paste(piece, (0, y))
        y += CLEAN_TO
        flip = not flip
    return p


canvas.paste(panel((0, PANEL)), (0, 0))
canvas.paste(panel((W - PANEL, W)), (W - PANEL, 0))

# soft shadow from the monitor frame onto the panels, then the black frame + opening
shadow = Image.new("L", (W, H), 0)
ImageDraw.Draw(shadow).rounded_rectangle((PANEL - 18, -40, W - PANEL + 18, H + 40), radius=40, fill=200)
shadow = shadow.filter(ImageFilter.GaussianBlur(18))
canvas = Image.composite(Image.new("RGBA", (W, H), (0, 0, 0, 255)), canvas, shadow)
d = ImageDraw.Draw(canvas)
d.rounded_rectangle((PANEL - 4, -20, W - PANEL + 4, H + 20), radius=36, fill=(8, 8, 8, 255))
d.rounded_rectangle((PANEL + 6, -10, W - PANEL - 6, H + 10), radius=30, outline=(38, 38, 42, 255), width=3)
mask = Image.new("L", (W, H), 255)
ImageDraw.Draw(mask).rounded_rectangle((L, T, W - R, H - B), radius=22, fill=0)
canvas.putalpha(mask)

# logo: title on the left panel, zombie (right part of the marquee) on the right panel
logo = Image.open(LOGO).convert("RGBA")
bbox = logo.getbbox()
logo = logo.crop(bbox)
# split at the emptiest column between the title and the zombie (right part of the marquee)
alpha = logo.split()[3]
cols = [sum(alpha.crop((x, 0, x + 1, logo.height)).getdata()) for x in range(logo.width)]
lo, hi = int(logo.width * 0.70), int(logo.width * 0.90)
cut = min(range(lo, hi), key=lambda x: cols[x])
title, zombie = logo.crop((0, 0, cut, logo.height)), logo.crop((cut, 0, logo.width, logo.height))
tw = PANEL - 26
title = title.resize((tw, int(title.height * tw / title.width)), Image.LANCZOS)
zh = 330
zombie = zombie.resize((int(zombie.width * zh / zombie.height), zh), Image.LANCZOS)


def glow(img, radius=10):
    g = Image.new("RGBA", img.size, (0, 0, 0, 0))
    g.putalpha(img.split()[3].filter(ImageFilter.GaussianBlur(radius)))
    return g


tx, ty = (PANEL - title.width) // 2, H - title.height - 70
zx, zy = W - PANEL + (PANEL - zombie.width) // 2, H - zh - 40
for img, x, y in ((title, tx, ty), (zombie, zx, zy)):
    canvas.alpha_composite(glow(img), (x, y))
    canvas.alpha_composite(img, (x, y))

canvas.save(OUT)
json.dump({"width": W, "height": H, "top": T, "left": L, "bottom": B, "right": R,
           "opacity": 1.0, "messagex": 0.22, "messagey": 0.12},
          open(OUT[:-4] + ".info", "w", newline="\n"), indent=3)
print("wrote", OUT)
