#!/usr/bin/env python3
# Local Sinden light-gun tweaks for this Batocera cabinet. Batocera updates
# reset /usr, so this re-applies them on every boot (idempotent).
#
#   1. Thinner gun borders: thin=0.25%  medium=0.5%  big=1%
#      (Batocera defaults: 1% / 2% / 2%+1% black). Two code paths:
#      bezels.py (libretro) and gun_borders.py (standalone emulators).
#   2. Gun low-latency profile (Emulator.py): for gun games only -> no shader,
#      run-ahead 1 (2nd instance), 2 swapchain images, GPU hard sync.
#      Per-game settings still win.  Off switch, no file edits needed:
#          batocera-settings-set global.gun_lowlatency 0
#   3. Sinden tracker processes launched at nice -10 (virtual-sindenlightgun-add)
#      so heavy emulators can't starve them.
#   4. Fixed P1/P2: Batocera orders guns by whichever virtual gun was created
#      first (a boot-time race). Each virtual gun now carries its physical
#      gun's USB product id, configgen sorts guns by it, and at boot the
#      virtual guns are re-created in that order so emulators that enumerate
#      devices themselves agree. Order (P1 first):
#          controllers.guns.order=0f39,0f01     (in batocera.conf)
#      All three paths (libretro, standalone, ES menu -- see 6) round UP, so a
#      size is the same number of pixels in the menu and in every game
#      (1920 wide: thin 5px, medium 10px, big 20px).
#   5. Menu crosshair colors (EmulationStation binary, _gunAimColors table):
#      P1 red, P2 blue (default cyan / magenta) to match the guns. Rest of the
#      table reordered so a trigger pull still tints the aim (P1 orange,
#      P2 light blue). Patched first thing (ES starts ~1s after this service);
#      if ES already runs the old binary it is restarted via its /quit API
#      (never during a game). Byte patterns must match exactly once.
#   6. Menu border sizes (EmulationStation binary, Window::renderSindenBorders)
#      = the in-game sizes: thin 0.25%, medium/auto 0.5%, big 1% with no black
#      outer ring (ES defaults 1% / 2% / 2%+1%). Each size is a `mulss` reading
#      a float constant; the patch re-points it at an existing constant with
#      the wanted value (0.0025 / 0.005 / 0.01 / 0.0 already in ES's pool).
#   7. Gun games: NVIDIA OpenGL frame queue = 1 (__GL_MaxFramesAllowed) for
#      every OpenGL emulator (same gun_lowlatency switch as 2).
#   8. Supermodel: per-game switch to drop the render thread (-no-gpu-thread,
#      one frame less lag) where the game still holds 60 fps:
#          model3["lostwsga.zip"].supermodel_gputhread=0
#  10. ES gun list (gamesdb.xml): adds "hotdo" (HotD original, run 2-gun on MAME)
#      so it is gun-flagged + in the Lightgun collection; ES restarted once if it
#      already read the unpatched file.
#  11. MAME (MESS) controls: skip keypad controls mapped to buttons the pad lacks
#      (astrocade crashed configgen with KeyError 'r3' on the I-PAC).
#  12. Flatpak apps (Jellyfin): Hotkey+Start exit falls back to flatpak-exit.sh when
#      `flatpak ps` is empty (no systemd user session on Batocera), and a relaunch
#      guard swallows the launch ES fires ~3 s after such an exit.
#  13. Moonlight: if another gamepad (e.g. Xbox) is connected at launch, hide the I-PAC
#      from Moonlight only, so that pad is player 1 on the PC. Global P1/P2 untouched.
#   9. File installs (INSTALL list): hlsl-flat.json, MAME's hlsl CRT chain with
#      the geometry distortion zeroed (gun-safe) -> bgfxshaders=hlsl-flat.
#
# Run from:  /userdata/system/services/gun_patches (S99userservices; lives in
#            /userdata so it survives updates). Not from /boot/boot-custom.sh:
#            S00 runs before /userdata is mounted, and updates delete it anyway.
#
# Usage:  patch-gun-borders.py             apply + log status
#         patch-gun-borders.py --status    log status only
#         patch-gun-borders.py --revert    undo every file edit
# Log:    /userdata/system/logs/gun-patches.log
import hashlib, json, os, subprocess, struct, sys, time, pathlib, urllib.request


def _configgen():
    # don't hardcode python3.12: a Batocera update may move to a newer python
    found = sorted(pathlib.Path("/usr/lib").glob("python3*/site-packages/configgen"))
    return str(found[-1]) if found else "/usr/lib/python3.12/site-packages/configgen"


CG = _configgen()
ES = "/usr/bin/emulationstation"
ES_PRISTINE = "/overlay/base/usr/bin/emulationstation"   # read-only squashfs layer
ES_CACHE = pathlib.Path("/userdata/system/gun-patches-es.json")
GAMESDB = "/usr/share/emulationstation/resources/gamesdb.xml"
ES_FEATURES = "/usr/share/emulationstation/es_features.cfg"
CHANGED = set()          # features patched during this run (ES may need a restart)
LOG = pathlib.Path("/userdata/system/logs/gun-patches.log")

LOWLAT = """        # >>> local patch: gun low-latency
        if str(system_data.get('use_guns', '')).lower() in ('1', 'true') \\
                and str(system_data.get('gun_lowlatency', '1')) != '0':
            # gun-safe twin of the system's koko mask (flat: no curvature/bezel/zoom,
            # so the aim is exact); anything else -> no shader
            _sh = str(system_data.get('shaderset', ''))
            _gunshader = f'{_sh} [guns]' if _sh.startswith('Koko CRT - ') and not _sh.endswith('[guns]') else 'none'
            for k, v in (('shaderset', _gunshader),
                         ('runahead', '1'),
                         ('secondinstance', '1'),
                         ('retroarch.video_max_swapchain_images', '2'),
                         ('retroarch.video_hard_sync', 'true'),
                         ('retroarch.video_hard_sync_frames', '0')):
                if k not in game_settings:
                    system_data[k] = v
            # no koko shader in gun games -> the koko-cut art set would leave a gap:
            # use the original (uncut) art + core aspect; the Sinden border goes on it
            if system_data.get('bezel') == 'thebezelproject-koko' and 'bezel' not in game_settings:
                system_data['bezel'] = 'thebezelproject'
                if 'ratio' not in game_settings:
                    system_data['ratio'] = 'core'
        # <<< local patch: gun low-latency

"""
ANCHOR = "        # network options\n"
# inserted just BEFORE the low-latency block, so LOWLAT + ANCHOR stay contiguous
GLQ_ANCHOR = "        # >>> local patch: gun low-latency\n"
GLQ = """        # >>> local patch: gun GL frame queue
        # NVIDIA OpenGL queues up to 2-3 frames ahead by default; 1 = the least lag.
        # Covers every OpenGL emulator (Dolphin, Supermodel, Model 2/wine, Lindbergh,
        # Singe...); Vulkan ones (PCSX2, RPCS3) ignore it. Gun games only.
        if str(system_data.get('use_guns', '')).lower() in ('1', 'true') \\
                and str(system_data.get('gun_lowlatency', '1')) != '0':
            import os as _os
            _os.environ.setdefault('__GL_MaxFramesAllowed', '1')
        # <<< local patch: gun GL frame queue

"""
SM_ANCHOR = '        commandArray: list[str | Path] = ["supermodel", "-fullscreen", "-channels=2"]\n'
SM_GPU = """        # >>> local patch: supermodel gpu thread
        # the render thread adds a frame of lag; per-game switch for games that still
        # hold 60 fps without it:  model3["<rom>.zip"].supermodel_gputhread=0
        if system.config.get('supermodel_gputhread') == '0':
            commandArray.append('-no-gpu-thread')
        # <<< local patch: supermodel gpu thread
"""
LAUNCH = "PATH=/bin:/sbin:/usr/bin:/usr/sbin nohup mono-service"
DEFAULT_ORDER = "0f39,0f01"
VOUT = '--output name="Sinden lightgun" >/dev/null'
VOUT_ID = ('--output name="Sinden lightgun" '
           'device-id=0000:$(cat /sys/class/input/$(basename "${DEV1}")/device/id/product) >/dev/null')
GUNSORT_ANCHOR = "        if not guns:\n            _logger.info('no gun found')\n"
GUNSORT = """        # >>> local patch: stable gun order (P1/P2 by physical gun id, not detection order)
        try:
            _order = ['0f39', '0f01']
            for _l in Path('/userdata/system/batocera.conf').read_text().splitlines():
                if _l.startswith('controllers.guns.order='):
                    _order = [x.strip().lower() for x in _l.split('=', 1)[1].split(',') if x.strip()]

            def _rank(g: Gun) -> int:
                pid = Path(f'/sys/class/input/{Path(g.node).name}/device/id/product').read_text().strip().lower()
                return _order.index(pid) if pid in _order else len(_order)

            guns.sort(key=_rank)  # stable: unknown guns keep detection order
            _logger.info('gun order: %s', [g.node for g in guns])
        except Exception as e:
            _logger.warning('gun order patch failed: %s', e)
        # <<< local patch: stable gun order

"""

# ---------------------------------------------------------------------------
# EmulationStation binary patches. Located by analysing the pristine binary
# (not hardcoded offsets), so they follow a recompiled ES after an update.
# Any surprise -> PlanError -> logged FAIL, file left untouched.
# ---------------------------------------------------------------------------
# ES vertex colors are 0xAABBGGRR. _gunAimColors = 6 colors: a 16-byte constant
# (cyan, magenta, yellow, red) + an 8-byte one (blue, green).
RED, BLUE, GREEN, YELLOW, CYAN, MAGENTA = (0xFF0000FF, 0xFFFF0000, 0xFF00FF00,
                                           0xFF00FFFF, 0xFFFFFF00, 0xFFFF00FF)
AIM_A_OLD = struct.pack("<4I", CYAN, MAGENTA, YELLOW, RED)
AIM_A_NEW = struct.pack("<4I", RED, BLUE, GREEN, YELLOW)     # P1 red, P2 blue
AIM_B_OLD = struct.pack("<2I", BLUE, GREEN)
AIM_B_NEW = struct.pack("<2I", CYAN, MAGENTA)                # trigger tint: P1 orange, P2 light blue
F32 = lambda v: struct.pack("<f", v)
MULSS_RIP = b"\xf3\x0f\x59\x05"                               # mulss xmm0, [rip+disp32]


class PlanError(Exception):
    pass


class Elf:
    def __init__(self, b):
        if b[:5] != b"\x7fELF\x02":
            raise PlanError("not an ELF64 binary")
        self.b = b
        phoff, = struct.unpack_from("<Q", b, 0x20)
        phentsz, phnum = struct.unpack_from("<HH", b, 0x36)
        self.segs = []
        for k in range(phnum):
            t, fl, off, va, _, fsz = struct.unpack_from("<IIQQQQ", b, phoff + k * phentsz)
            if t == 1:                       # PT_LOAD
                self.segs.append((off, va, fsz, fl))
        self.code = [s for s in self.segs if s[3] & 1]
        self.rodata = [s for s in self.segs if not s[3] & 3]  # R only

    def o2v(self, o):
        for off, va, sz, _ in self.segs:
            if off <= o < off + sz:
                return va + o - off
        raise PlanError(f"offset {o:#x} not mapped")

    def v2o(self, v):
        for off, va, sz, _ in self.segs:
            if va <= v < va + sz:
                return off + v - va
        raise PlanError(f"address {v:#x} not mapped")

    def rip(self, o, insn_len):
        """target file offset of a rip-relative insn at o whose disp32 is its last 4 bytes"""
        disp, = struct.unpack_from("<i", self.b, o + insn_len - 4)
        return self.v2o(self.o2v(o) + insn_len + disp)

    def find_all(self, pat, segs, align=1):
        out = []
        for off, _, sz, _ in segs:
            i = self.b.find(pat, off, off + sz)
            while i >= 0:
                if i % align == 0:
                    out.append(i)
                i = self.b.find(pat, i + 1, off + sz)
        return out

    def str_refs(self, s):
        """code offsets just after each insn loading the address of C string s
        (mov esi, imm32 in non-PIE builds, or lea rsi, [rip+x])"""
        hits = [o + 1 for o in self.find_all(b"\0" + s + b"\0", self.rodata)]
        if not hits:
            raise PlanError(f"string {s!r} not found")
        ends = []
        for so in hits:
            va = self.o2v(so)
            if va < 2 ** 32:
                ends += [o + 5 for o in self.find_all(b"\xbe" + struct.pack("<I", va), self.code)]
            for o in self.find_all(b"\x48\x8d\x35", self.code):
                try:
                    if self.rip(o, 7) == so:
                        ends.append(o + 7)
                except PlanError:
                    pass
        return sorted(ends)

    def cond_target(self, start, limit=0x30):
        """after `call strcmp-ish; test al,al; jne X` return (X, offset after jne)"""
        b = self.b
        for o in range(start, start + limit):
            if b[o:o + 2] == b"\x84\xc0":
                if b[o + 2:o + 4] == b"\x0f\x85":
                    rel, = struct.unpack_from("<i", b, o + 4)
                    return o + 8 + rel, o + 8
                if b[o + 2] == 0x75:
                    return o + 4 + struct.unpack_from("<b", b, o + 3)[0], o + 4
        raise PlanError("expected string-compare branch not found")

    def mulss(self, start, limit, n):
        out = [o for o in range(start, start + limit) if self.b[o:o + 4] == MULSS_RIP][:n]
        if len(out) != n:
            raise PlanError(f"expected {n} mulss at {self.o2v(start):#x}, found {len(out)}")
        return out

    def rip_xrefs(self, targets):
        """count rip-relative references (disp32 ending an insn) to each target offset"""
        tv = {self.o2v(t): t for t in targets}
        count = dict.fromkeys(targets, 0)
        b = self.b
        for off, va, sz, _ in self.code:
            unpack = struct.Struct("<i").unpack_from
            for i in range(off, off + sz - 4):
                t = va + (i - off) + 4 + unpack(b, i)[0]
                if t in tv:
                    count[tv[t]] += 1
        return count


def _unique_edit(b, off, old, new):
    """(old, new) byte patterns around an edit at off, widened with context
    until the original is unique in the file and the result absent from it"""
    for ctx in (4, 8, 12, 16, 24, 32):
        s, e = off - ctx, off + len(old) + ctx
        o_pat = b[s:e]
        n_pat = b[s:off] + new + b[off + len(old):e]
        if b.count(o_pat) == 1 and b.count(n_pat) == 0:
            return o_pat, n_pat
    raise PlanError(f"no unique pattern at {off:#x}")


def es_plan_colors(elf):
    b = elf.b
    a = elf.find_all(AIM_A_OLD, elf.rodata)
    if len(a) != 1:
        raise PlanError(f"crosshair color table: {len(a)} matches (want 1)")
    a = a[0]
    # the initializer: movdqa/movaps xmm, [rip+A] then mov r64, [rip+B] shortly after
    ref = None
    for pat, ln in ((b"\x66\x0f\x6f", 8), (b"\x0f\x28", 7), (b"\x0f\x10", 7), (b"\xf3\x0f\x6f", 8)):
        for o in elf.find_all(pat, elf.code):
            modrm = b[o + len(pat)]
            if modrm & 0xC7 == 0x05 and elf.rip(o, ln) == a:
                ref = o + ln
                break
        if ref:
            break
    if ref is None:
        raise PlanError("crosshair color table: initializer not found")
    bo = None
    for o in range(ref, ref + 0x20):
        if b[o] in (0x48, 0x4c) and b[o + 1] == 0x8b and b[o + 2] & 0xC7 == 0x05:
            bo = elf.rip(o, 7)
            break
    if bo is None or b[bo:bo + 8] != AIM_B_OLD:
        raise PlanError("crosshair color table: second half not where expected")
    refs = elf.rip_xrefs([a, bo])
    if refs[a] != 1 or refs[bo] != 1:
        raise PlanError(f"crosshair color constants shared with other code ({refs[a]}, {refs[bo]} refs)")
    return [_unique_edit(b, a, AIM_A_OLD, AIM_A_NEW), _unique_edit(b, bo, AIM_B_OLD, AIM_B_NEW)]


def es_plan_borders(elf):
    b = elf.b
    size_refs = elf.str_refs(b"controllers.guns.borderssize")
    thin_refs = elf.str_refs(b"thin")
    big_refs = elf.str_refs(b"big")
    plans = []
    for sr in size_refs:
        thin = [t for t in thin_refs if 0 < t - sr < 0x80]
        if not thin:
            continue
        big = [g for g in big_refs if 0 < g - thin[0] < 0x40]
        if not big:
            continue
        try:
            thin_blk, _ = elf.cond_target(thin[0])
            big_blk, default_blk = elf.cond_target(big[0])
            d, = elf.mulss(default_blk, 0x60, 1)
            t, = elf.mulss(thin_blk, 0x40, 1)
            bo, bi = elf.mulss(big_blk, 0x100, 2)
        except PlanError:
            continue                          # e.g. the settings-menu code, not the renderer
        plans.append((d, t, bo, bi))
    if len(plans) != 1:
        raise PlanError(f"menu border renderer: {len(plans)} candidates (want 1)")
    d, t, bo, bi = plans[0]
    want = ((d, 0.02, 0.005), (t, 0.01, 0.0025), (bo, 0.01, 0.0), (bi, 0.02, 0.01))
    edits = []
    for insn, cur, new in want:
        tgt = elf.rip(insn, 8)
        if b[tgt:tgt + 4] != F32(cur):
            raise PlanError(f"menu border: {elf.o2v(insn):#x} multiplies by "
                            f"{struct.unpack_from('<f', b, tgt)[0]:g}, expected {cur}")
        cands = elf.find_all(F32(new), elf.rodata, align=4)
        if not cands:
            raise PlanError(f"menu border: no {new} constant to point at")
        dst = min(cands, key=lambda c: abs(c - tgt))   # nearest = same constant pool
        disp = elf.o2v(dst) - (elf.o2v(insn) + 8)
        if not -2 ** 31 <= disp < 2 ** 31:
            raise PlanError("menu border: constant out of reach")
        edits.append(_unique_edit(b, insn + 4, b[insn + 4:insn + 8], struct.pack("<i", disp)))
    return edits


_PLAN = None


def es_plan():
    """{'colors': [(old, new)...], 'borders': [...]} for the installed ES, cached
    per pristine binary (analysis takes a few seconds; boot must be fast)"""
    global _PLAN
    if _PLAN is None:
        _PLAN = _es_plan()
    return _PLAN


def _es_plan():
    src = pathlib.Path(ES_PRISTINE if os.path.exists(ES_PRISTINE) else ES)
    b = src.read_bytes()
    key = hashlib.sha1(b).hexdigest()
    try:
        c = json.loads(ES_CACHE.read_text())
        if c.get("sha1") == key:
            return {k: [(bytes.fromhex(o), bytes.fromhex(n)) for o, n in v] if isinstance(v, list) else v
                    for k, v in c["plan"].items()}
    except (OSError, ValueError, KeyError):
        pass
    plan = {}
    try:
        elf = Elf(b)
    except PlanError as e:
        return {"colors": str(e), "borders": str(e)}
    for name, fn in (("colors", es_plan_colors), ("borders", es_plan_borders)):
        try:
            plan[name] = fn(elf)
        except PlanError as e:
            plan[name] = str(e)
        except Exception as e:               # malformed/unexpected binary: never crash the boot run
            plan[name] = f"analysis error {type(e).__name__}: {e}"
    try:
        ES_CACHE.write_text(json.dumps({"sha1": key, "source": str(src), "plan": {
            k: [(o.hex(), n.hex()) for o, n in v] if isinstance(v, list) else v
            for k, v in plan.items()}}, indent=1))
    except OSError:
        pass
    return plan


def es_subs(name):
    def get():
        v = es_plan()[name]
        if isinstance(v, str):
            raise PlanError(v)
        return v
    return get


# (feature, file, [(original, patched), ...]) -- str = text file, bytes = binary;
# a callable returns that list (or raises PlanError).
# ES first: it has to win the race against ES starting.
PATCHES = [
    ("menu-crosshair-colors", ES, es_subs("colors")),
    ("menu-border-sizes", ES, es_subs("borders")),
    ("borders-libretro", f"{CG}/utils/bezels.py", [
        ('if bordersSize == "thin":\n        return 1, 0',
         'if bordersSize == "thin":\n        return 0.25, 0'),
        ('if bordersSize == "medium":\n        return 2, 0',
         'if bordersSize == "medium":\n        return 0.5, 0'),
        ('if bordersSize == "big":\n        return 2, 1',
         'if bordersSize == "big":\n        return 1, 0'),
        # int() for fractional percents, and round UP like gun_borders.py and ES
        ("outerBorderSize = w * outerBorderSizePer // 100",
         "outerBorderSize = int(-(-w * outerBorderSizePer // 100))"),
        ("innerBorderSize = w * innerBorderSizePer // 100",
         "innerBorderSize = int(-(-w * innerBorderSizePer // 100))"),
        ("return (w * (innerBorderSizePer + outerBorderSizePer)) // 100",
         "return int(-(-w * (innerBorderSizePer + outerBorderSizePer) // 100))"),
    ]),
    ("borders-standalone", f"{CG}/utils/gun_borders.py", [
        ('"thin":   (1, 0),',   '"thin":   (0.25, 0),'),
        ('"medium": (2, 0),',   '"medium": (0.5, 0),'),
        ('"big":    (2, 1),',   '"big":    (1, 0),'),
    ]),
    # ES's gun list lacks MAME's "hotdo" (House of the Dead, original release; only the
    # prototype hotdp is listed) -> the 2-gun MAME entry model2/hotdo.zip was neither
    # auto gun-flagged nor in the Lightgun collection. ES reads this file at startup.
    ("gamesdb-guns", GAMESDB, [
        ('    <game id="hotdp">\n', '    <game id="hotdo">\n      <gun/>\n    </game>\n    <game id="hotdp">\n'),
        # Wii: ES matches gamesdb ids against the rom FILE name as a prefix (lowercase, letters
        # and digits only). "House of the Dead 2 & 3 Return, The (USA)..." gives
        # "houseofthedead23return..." ("&" is dropped), so the "houseofthedead2and3" entry never
        # matched: no Sinden/Wiimote gun setup in Dolphin. Same gun values under the matching id.
        ('    <game id="houseofthedead2and3">\n',
         '    <game id="houseofthedead23return">\n      <gun vertical_offset="15" yaw="19" pitch="19"/>\n'
         '    </game>\n    <game id="houseofthedead2and3">\n'),
    ]),
    # MESS systems (astrocade keypad...) map controls onto buttons an arcade panel lacks
    # (BUTTON9/10 -> l3/r3): configgen crashed with KeyError and the game never started.
    # Skip controls whose button isn't on the pad.
    ("mame-missing-buttons", f"{CG}/generators/mame/mameControllers.py", [
        ("                if nplayer == thisControl['player'] and xml_input_alt is not None and config_alt is not None:\n",
         "                if nplayer == thisControl['player'] and xml_input_alt is not None and config_alt is not None \\\n"
         "                        and all(mappings_use.get(thisControl[k]) in pad.inputs\n"
         "                                for k in ('useMapping', 'useMapping1', 'useMapping2') if k in thisControl):\n"),
    ]),
    # Hotkey+Start couldn't close Flatpak apps (Jellyfin): `flatpak ps` lists nothing without
    # a systemd user session, so `flatpak kill` got no app id. Fall back to a helper that
    # stops the app inside its sandbox (NOT emukill: it sends "exit" back here -> loop).
    ("flatpak-exit", f"{CG}/generators/flatpak/flatpakGenerator.py", [
        ('"exit": "flatpak kill $(flatpak ps --columns=application | head -n 1)"',
         '"exit": "flatpak kill $(flatpak ps --columns=application | head -n 1) 2>/dev/null || /userdata/system/flatpak-exit.sh"'),
    ]),
    # ES launches a Flatpak app again ~3 s after it was closed with Hotkey+Start (not after a
    # plain kill, not for RetroArch games). flatpak-exit.sh stamps the time; a launch of any
    # Flatpak app within 10 s of that stamp becomes a no-op (/bin/true) -> nothing reappears.
    ("flatpak-relaunch-guard", f"{CG}/generators/flatpak/flatpakGenerator.py", [
        ("        # needed for SteamLink and other few flatpak apps with the same issue\n",
         "        # >>> local patch: flatpak relaunch guard\n"
         "        try:\n"
         "            import time as _t\n"
         "            _st = '/tmp/flatpak-exit.stamp'\n"
         "            if os.path.exists(_st) and _t.time() - os.path.getmtime(_st) < 10:\n"
         "                os.remove(_st)\n"
         "                commandArray = ['/bin/true']\n"
         "        except OSError:\n"
         "            pass\n"
         "        # <<< local patch: flatpak relaunch guard\n"
         "        # needed for SteamLink and other few flatpak apps with the same issue\n"),
    ]),
    # Moonlight: the I-PAC (2 joysticks = P1/P2) takes the PC's gamepad slots 1-2, so an Xbox
    # pad lands on player 3. Only while Moonlight runs, and only if another gamepad is
    # connected at launch, hide the I-PAC from Moonlight (SDL ignore list). ES, emulators and
    # the global P1/P2 order are never touched; without an extra pad nothing changes.
    ("moonlight-pad-priority", f"{CG}/generators/moonlight/moonlightGenerator.py", [
        ('        return Command.Command(\n'
         '            array=command,\n'
         '            env={\n'
         '                "XDG_DATA_DIRS": CONFIGS,\n'
         '                "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(playersControllers),\n'
         '                "SDL_JOYSTICK_HIDAPI": "0"\n'
         '            }\n'
         '        )\n',
         '        # >>> local patch: moonlight pad priority\n'
         '        _env = {\n'
         '            "XDG_DATA_DIRS": CONFIGS,\n'
         '            "SDL_GAMECONTROLLERCONFIG": generate_sdl_game_controller_config(playersControllers),\n'
         '            "SDL_JOYSTICK_HIDAPI": "0"\n'
         '        }\n'
         '        try:\n'
         '            import re as _re\n'
         '            _devs = open("/proc/bus/input/devices").read().split("\\n\\n")\n'
         '            _pads = [d for d in _devs if _re.search(r"Handlers=.*\\bjs\\d", d)\n'
         '                     and not _re.search(r"(?i)sinden|lightgun", d)]  # guns are not gamepads\n'
         '            if any("Vendor=d209" not in d for d in _pads) and any("Vendor=d209" in d for d in _pads):\n'
         '                _env["SDL_GAMECONTROLLER_IGNORE_DEVICES"] = "0xd209/0x0421"\n'
         '        except OSError:\n'
         '            pass\n'
         '        # <<< local patch: moonlight pad priority\n'
         '        return Command.Command(\n'
         '            array=command,\n'
         '            env=_env\n'
         '        )\n'),
    ]),
    # Moonlight bitrate: Batocera only offers 5/10/20/50 Mbps. Over Wi-Fi 20 triggers
    # "slow connection" now and then and 10 is soft at 1080p60 -> add 15 Mbps (value "4").
    # The menu choice lives in es_features.cfg (read by ES at startup).
    ("moonlight-bitrate-15", f"{CG}/generators/moonlight/moonlightGenerator.py", [
        ('            case "2":\n'
         "                bitrate = '20000'\n",
         '            case "4":\n'
         "                bitrate = '15000'\n"
         '            case "2":\n'
         "                bitrate = '20000'\n"),
    ]),
    ("moonlight-bitrate-15-menu", ES_FEATURES, [
        ('      <choice name="10Mbps (good for 1080p 60FPS)" value="1" />\n',
         '      <choice name="10Mbps (good for 1080p 60FPS)" value="1" />\n'
         '      <choice name="15Mbps (Wi-Fi friendly 1080p 60FPS)" value="4" />\n'),
    ]),
    # Sinden menu RECOIL: "machinegun" / "machinegun-quiet" are relabelled AUTO - MEDIUM /
    # AUTO - HARD (en_US translation below; "gun-quiet" = AUTO - SOFT, strength 40). The
    # tracker uses them as the base (menus, boot, replug) = mixed mode (repeat after 150 ms) at
    # strength 70 / 100; sinden-recoil/recoilctl.py switches modes per game at that strength.
    ("recoil-auto-levels", "/usr/bin/virtual-sindenlightgun-add", [
        ('\t    "machinegun")\n\t\tRECOIL_ENABLED=1\n\t\tRECOIL_NORMALORREPEAT=1\n'
         '\t\tRECOIL_STRENGTH=100\n\t\tRECOIL_STARTDELAY=1\n',
         '\t    "machinegun")\n\t\tRECOIL_ENABLED=1\n\t\tRECOIL_NORMALORREPEAT=1\n'
         '\t\tRECOIL_STRENGTH=70\n\t\tRECOIL_STARTDELAY=150\n'),
        ('\t    "machinegun-quiet")\n\t\tRECOIL_ENABLED=1\n\t\tRECOIL_NORMALORREPEAT=1\n'
         '\t\tRECOIL_STRENGTH=40\n\t\tRECOIL_STARTDELAY=1\n',
         '\t    "machinegun-quiet")\n\t\tRECOIL_ENABLED=1\n\t\tRECOIL_NORMALORREPEAT=1\n'
         '\t\tRECOIL_STRENGTH=100\n\t\tRECOIL_STARTDELAY=150\n'),
    ]),
    ("gun-lowlatency", f"{CG}/Emulator.py", [
        (ANCHOR, LOWLAT + ANCHOR),
    ]),
    ("gun-gl-queue", f"{CG}/Emulator.py", [
        (GLQ_ANCHOR, GLQ + GLQ_ANCHOR),
    ]),
    ("supermodel-gputhread", f"{CG}/generators/supermodel/supermodelGenerator.py", [
        (SM_ANCHOR, SM_ANCHOR + SM_GPU),
    ]),
    ("tracker-priority", "/usr/bin/virtual-sindenlightgun-add", [
        (LAUNCH, LAUNCH.replace("nohup", "nice -n -10 nohup")),
    ]),
    ("gun-id-tag", "/usr/bin/virtual-sindenlightgun-add", [
        (VOUT, VOUT_ID),
    ]),
    ("gun-order", f"{CG}/gun.py", [
        (GUNSORT_ANCHOR, GUNSORT + GUNSORT_ANCHOR),
    ]),
]


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def write(p, data):
    # write a temp file + rename: works on running binaries (no ETXTBSY) and a
    # concurrent reader never sees a half-written file
    mode = p.stat().st_mode
    tmp = p.with_name(p.name + ".patch-tmp")
    if isinstance(data, bytes):
        tmp.write_bytes(data)
    else:
        tmp.write_text(data)
    os.chmod(tmp, mode)
    os.replace(tmp, p)
    for pyc in (p.parent / "__pycache__").glob(p.stem + ".*.pyc"):
        try: pyc.unlink()
        except OSError: pass


def run(action):
    ok = True
    for feature, path, subs in PATCHES:
        p = pathlib.Path(path)
        if not p.exists():
            log(f"FAIL {feature}: {path} missing"); ok = False; continue
        if callable(subs):
            try:
                subs = subs()
            except Exception as e:           # PlanError or anything unexpected: skip, keep going
                log(f"FAIL {feature}: {e} - left untouched"); ok = False; continue
        binary = isinstance(subs[0][0], bytes)
        s = orig = p.read_bytes() if binary else p.read_text()
        states = []
        for old, new in subs:
            patched = new in s
            src, dst = (new, old) if action == "revert" else (old, new)
            if patched != (action == "revert"):
                states.append("done")          # already in wanted state
            elif action == "status":
                states.append("OFF")
            elif binary and s.count(src) != 1:
                states.append("MISSING")      # never patch an ambiguous binary match
            elif src in s:
                s = s.replace(src, dst, 1); states.append("changed")
            else:
                states.append("MISSING")      # code changed upstream?
        if binary and "MISSING" in states:
            s = orig                           # all-or-nothing for binaries
        if s != orig:
            write(p, s)
            if p.suffix == ".py":
                r = subprocess.run([sys.executable, "-m", "py_compile", str(p)])
                if r.returncode != 0:          # never leave broken code behind
                    write(p, orig)
                    log(f"FAIL {feature}: compile error, restored original"); ok = False; continue
        if "OFF" in states:
            ok = False
            log(f"OFF  {feature}: not applied")
        elif "MISSING" in states:
            ok = False
            log(f"FAIL {feature}: pattern not found in {p.name} "
                f"({states.count('MISSING')}/{len(states)}) - Batocera code changed, patch needs updating")
        else:
            if "changed" in states:
                CHANGED.add(feature)
            verb = {"apply": "applied", "revert": "reverted", "status": "active"}[action]
            log(f"OK   {feature}: {verb}" + (" (just now)" if "changed" in states else ""))
    return ok


def nice_of(stat_path):
    # comm can contain spaces/parens, so split after the LAST ')'; nice = field 19
    return int(pathlib.Path(stat_path).read_text().rsplit(")", 1)[1].split()[16])


def renice_trackers(want):
    # Running trackers don't pick up the launcher edit until a gun replug.
    # renice sets ABSOLUTE values; shift each thread by the same delta so
    # mono's own lower-priority housekeeping threads keep their offset.
    out = subprocess.run(["pgrep", "-f", "LightgunMono-.*exe"], capture_output=True, text=True).stdout
    for pid in out.split():
        try:
            if pathlib.Path(f"/proc/{pid}/comm").read_text().strip() != "mono":
                continue
            cur = nice_of(f"/proc/{pid}/stat")
        except (OSError, ValueError, IndexError):
            continue
        if cur == want:
            continue
        for t in pathlib.Path(f"/proc/{pid}/task").glob("*"):
            try:
                target = max(-20, min(19, nice_of(t / "stat") + want - cur))
            except (OSError, ValueError, IndexError):
                continue
            subprocess.run(["renice", "-n", str(target), "-p", t.name], capture_output=True)
        log(f"OK   tracker-priority: pid {pid} nice {cur} -> {nice_of(f'/proc/{pid}/stat')}")


def game_running():
    # match the real launcher (python3 /usr/bin/emulatorlauncher ...), not any
    # shell whose command line merely mentions it
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")[:2]
        except OSError:
            continue
        if any(a.endswith(b"/emulatorlauncher") or a == b"emulatorlauncher" for a in argv):
            return True
    return False


def reload_es():
    """If EmulationStation still runs the pre-patch binary, restart it so the
    menu crosshair colors apply. Uses ES's own /quit (its launcher loop starts
    it again); never while a game is running."""
    stale, running = [], []
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            # (comm is truncated to 15 chars, so match the exe link instead)
            exe = os.readlink(p / "exe")
            if exe == ES + " (deleted)":
                stale.append(int(p.name))
            elif exe == ES:
                running.append(int(p.name))
        except OSError:
            pass
    # an ES that is already up read gamesdb.xml / es_features.cfg before we patched them just now
    if not stale and not (CHANGED & {"gamesdb-guns", "moonlight-bitrate-15-menu",
                                     "file emulationstation2.mo"} and running):
        return
    if game_running():
        log("INFO emulationstation: game running, menu patches apply at next ES restart")
        return
    err = None
    for _ in range(30):                        # ES may still be starting up
        try:
            urllib.request.urlopen("http://127.0.0.1:1234/quit", timeout=5).read()
            log("OK   emulationstation: restarted to load the menu patches")
            return
        except Exception as e:
            err = e
            time.sleep(1)
    log(f"FAIL emulationstation: restart via API failed ({err}), menu patches apply at next ES restart")


def gun_order():
    try:
        out = subprocess.run(["batocera-settings-get", "controllers.guns.order"],
                             capture_output=True, text=True).stdout.strip()
    except OSError:
        out = ""
    return [x.strip().lower() for x in (out or DEFAULT_ORDER).split(",") if x.strip()]


def sysattr(node, attr):
    try:
        return pathlib.Path(f"/sys/class/input/{pathlib.Path(node).name}/device/{attr}").read_text().strip().lower()
    except OSError:
        return ""


def virtual_guns():
    """event nodes of the virtual Sinden guns, in creation (= event number) order"""
    nodes = [f"/dev/input/{p.name}" for p in pathlib.Path("/sys/class/input").glob("event*")
             if sysattr(p.name, "name") == "sinden lightgun"]
    return sorted(nodes, key=lambda n: int(n.rsplit("event", 1)[1]))


def evsieve_guns():
    """{physical product id: (pid, argv)} for each running Sinden evsieve"""
    res = {}
    for p in pathlib.Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            argv = (p / "cmdline").read_bytes().split(b"\0")[:-1]
        except OSError:
            continue
        argv = [a.decode() for a in argv]
        if not argv or not argv[0].endswith("evsieve") or "name=Sinden lightgun" not in argv:
            continue
        phys = argv[argv.index("--input") + 1]
        res[sysattr(phys, "id/product")] = (int(p.name), argv)
    return res


def enforce_order():
    """Re-create the virtual guns so P1's gets the lowest event number.
    Only touches evsieve (the virtual device); the trackers keep running."""
    order = gun_order()
    current = [sysattr(n, "id/product") for n in virtual_guns()]
    procs = evsieve_guns()
    wanted = [pid for pid in order if pid in procs] + [p for p in procs if p not in order]
    if current == wanted:
        log(f"OK   gun-order-live: {current} (P1 first)")
        return
    if len(procs) != len(current):
        log(f"FAIL gun-order-live: {len(procs)} evsieve vs {len(current)} virtual guns, not touching")
        return
    for pid, _ in procs.values():
        os.kill(pid, 15)
    for _ in range(50):                      # wait for the virtual devices to go
        if not virtual_guns():
            break
        time.sleep(0.1)
    for prod in wanted:
        oldpid, argv = procs[prod]
        argv = [a for a in argv if not a.startswith("device-id=")]
        argv.insert(argv.index("name=Sinden lightgun") + 1, f"device-id=0000:{prod}")
        new = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
        # keep virtual-sindenlightgun-add's bookkeeping in sync
        for pf in pathlib.Path("/var/run").glob("virtual-sindenlightgun-devices.*.pid"):
            if pf.read_text().strip() == str(oldpid):
                pf.write_text(f"{new.pid}\n")
        for _ in range(50):                  # wait until it exists before the next one
            if prod in [sysattr(n, "id/product") for n in virtual_guns()]:
                break
            time.sleep(0.1)
    log(f"OK   gun-order-live: was {current}, re-created as {[sysattr(n, 'id/product') for n in virtual_guns()]}")


# Files kept in /userdata and copied into the (reset-on-boot) system tree.
# MAME only reads bgfx chains from its -bgfx_path (/usr/bin/mame/bgfx/chains).
INSTALL = [
    ("/userdata/system/mame-bgfx/hlsl-flat.json", "/usr/bin/mame/bgfx/chains/hlsl-flat.json"),
    # ES reads its English labels through gettext (LANG=en_US.UTF-8): relabels the Sinden
    # RECOIL choices QUIET GUN / MACHINE GUN / QUIET MACHINE GUN -> AUTO - SOFT / MEDIUM / HARD
    # (built by sinden-recoil/make-es-mo.py). ES loads it at startup -> restarted once.
    ("/userdata/system/sinden-recoil/emulationstation2.mo",
     "/usr/share/locale/en_US/LC_MESSAGES/emulationstation2.mo"),
]


def install_files(action):
    for src, dst in INSTALL:
        s, d = pathlib.Path(src), pathlib.Path(dst)
        name = f"file {d.name}"
        if action == "revert":
            if d.exists():
                d.unlink(); log(f"OK   {name}: removed")
            continue
        if not s.exists():
            log(f"FAIL {name}: source {src} missing"); continue
        if not d.parent.exists():
            log(f"FAIL {name}: {d.parent} missing - Batocera layout changed"); continue
        same = d.exists() and d.read_bytes() == s.read_bytes()
        if action == "status":
            log(f"{'OK  ' if same else 'OFF '} {name}: {'installed' if same else 'not installed'}")
        elif not same:
            d.write_bytes(s.read_bytes()); os.chmod(d, 0o644)
            CHANGED.add(name)
            log(f"OK   {name}: installed (just now)")
        else:
            log(f"OK   {name}: installed")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--revert":
        install_files("revert")
        run("revert")
        reload_es()
        renice_trackers(0)
        log("NOTE revert: also run 'batocera-services disable gun_patches', or it re-applies on next boot")
    elif arg == "--status":
        run("status")
        install_files("status")
        log(f"INFO gun-order-live: virtual guns {[sysattr(n, 'id/product') for n in virtual_guns()]}, wanted P1-first {gun_order()}")
    else:
        run("apply")
        install_files("apply")
        reload_es()
        renice_trackers(-10)
        enforce_order()
