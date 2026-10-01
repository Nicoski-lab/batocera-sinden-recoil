-- House of the Dead (MAME "hotdo", 2 Sinden guns): no white gun flash + no recoil on an empty clip.
-- Loaded by /userdata/system/configs/mame/hotdo.ini (autoboot_script). Log: /userdata/system/logs/hotd-sinden.log
--
-- * Flash: the original optical guns needed a white frame after every pull; with Sinden it's
--   only a distraction. Community "no flash screen" patch (egregiousguy, from model2emu's
--   hotd.lua: Romset_PatchDWord(ROMBASE, 0x18610, 0xa000000)): the i960 instruction at
--   program ROM 0x18610 becomes "ret" (0x0a000000). ROMBASE is 0 in MAME too (region
--   :maincpu == CPU space there). NOT the white-frame flag workram+0x430b0: forcing that to 0
--   also stopped P1's shots from registering.
-- * Empty clip: the gun firmware kicks on every trigger pull, it can't know the game's ammo.
--   P1/P2 ammo bytes (workram+0x1ec41 / +0x1ed0d, 6..0) are watched every frame; at 0 that gun's
--   recoil is switched off (Sinden cmd A1 0), back on as soon as the clip is refilled (A1 1).
--   Off-screen reload shots never kick anyway (RecoilTriggerOffscreen=0).
--   Recoil strength/mode stay whatever sinden-recoil set at game start (AUTO level).
-- * Rack to reload: racking a Sinden sends BTN_RIGHT = MAME GUNCODE_<n>_BUTTON2, which hotdo
--   doesn't use. On a rack, that player's gun is pointed off-screen (X axis minimum) with the
--   trigger held for 4 frames = the game's off-screen reload, then handed back. The synthetic
--   trigger never reaches the gun, so a rack doesn't kick. Recoil is switched back on at the
--   rack itself (the game refills the clip a few frames later; a shot right after racking
--   would otherwise get no kick), with a grace period so the still-empty clip can't switch it
--   off again.
-- * Exit combo (Batocera: hold trigger + rack + front-left 2 s): while rack + front-left are
--   both held, that gun's recoil is off - otherwise the held trigger fires the solenoid
--   repeatedly. sinden-recoil.sh keeps it off after the game until the triggers are released.
local NOFLASH_AT, RET = 0x18610, 0x0a000000
local ORIGINAL = 0x5ca01e00                 -- what the patch expects to replace (hotdo set)
local AMMO = { p1 = 0x1ec41, p2 = 0x1ed0d }
local PRODUCT = { p1 = "0f39", p2 = "0f01" } -- P1 red, P2 blue: never swap
local LOG = "/userdata/system/logs/hotd-sinden.log"
local RELOAD = {
  p1 = { rack = "GUNCODE_1_BUTTON2", start = "GUNCODE_1_BUTTON3", x = { ":P1_X", "Lightgun X" }, trig = { ":IN1", "P1 Trigger" } },
  p2 = { rack = "GUNCODE_2_BUTTON2", start = "GUNCODE_2_BUTTON3", x = { ":P2_X", "Lightgun X 2" }, trig = { ":IN1", "P2 Trigger" } },
}
local RELOAD_FRAMES = 4
local GRACE_FRAMES = 60                       -- after a rack: ignore "clip empty" for 1 s

local function log(s)
  local f = io.open(LOG, "a")
  if f then f:write(os.date("%H:%M:%S ") .. s .. "\n"); f:close() end
end

local function read(path)
  local f = io.open(path, "r")
  if not f then return nil end
  local s = f:read("*l"); f:close(); return s
end

local function gun_ports()
  local ports = {}
  local p = io.popen("ls /sys/class/tty 2>/dev/null")
  for tty in p:lines() do
    if tty:match("^ttyACM%d+$") then
      local usb = "/sys/class/tty/" .. tty .. "/device/../"
      local vid, pid = read(usb .. "idVendor"), read(usb .. "idProduct")
      for player, want in pairs(PRODUCT) do
        if vid == "16c0" and pid == want then ports[player] = "/dev/" .. tty end
      end
    end
  end
  p:close()
  return ports
end

local function recoil_setting()
  for l in io.lines("/userdata/system/batocera.conf") do
    local v = l:match("^controllers%.guns%.recoil=(.*)$")
    if v then return v end
  end
  return ""
end

local ports, recoil_on, state = {}, true, {}
local gun_on = {}                             -- what we last sent to each gun
-- Muting = the current mode's A2 frame (strength start strength pulse, saved by recoilctl in
-- /var/run/sinden-recoil.a2) resent with strength 0, NOT recoil off (A1 0): after A1 0 -> 1 the
-- gun swallowed the next pull, and a trigger released while off kept firing slow repeats.
local a2 = nil
local function load_a2()
  local f = io.open("/var/run/sinden-recoil.a2", "r")
  if not f then return end
  local v = {}
  for n in f:read("*a"):gmatch("%d+") do v[#v + 1] = tonumber(n) end
  f:close()
  if #v == 4 then a2 = v end
end

local function set_recoil(player, on, why)
  local port = ports[player]
  if not port or gun_on[player] == on then return end
  gun_on[player] = on
  local f = io.open(port, "wb")
  if not f then log("cannot open " .. port); return end
  if a2 then
    local s = on and a2[1] or 0
    f:write(string.char(0xAA, 0xA2, s, a2[2], s, a2[4], 0xBB))
  else
    f:write(string.char(0xAA, 0xA1, on and 1 or 0, 0, 0, 0, 0xBB))
  end
  f:close()
  log(player .. " kick " .. (on and "on" or "muted") .. (why and (" (" .. why .. ")") or ""))
end

local function field(spec)
  local port = manager.machine.ioport.ports[spec[1]]
  return port and port.fields[spec[2]]
end

-- rack-to-reload state per player: seq (input sequence), down (rack held), left (frames of
-- off-screen shot still to hold; 0 = idle)
local rack = {}
function hotd_reload(player)                  -- global: also used by the test wrapper
  local r = rack[player]
  if not r or r.left > 0 then return end
  local x, t = field(RELOAD[player].x), field(RELOAD[player].trig)
  if not (x and t) then return end
  x:set_value(x.minvalue); t:set_value(1)
  r.left = RELOAD_FRAMES
  r.grace = GRACE_FRAMES
  log(player .. " rack -> reload")
  if recoil_on and not r.combo then set_recoil(player, true, "rack"); state[player] = true end
end

local function rack_frame()
  local input = manager.machine.input
  for player, r in pairs(rack) do
    if r.left > 0 then
      r.left = r.left - 1
      if r.left == 0 then
        field(RELOAD[player].x):clear_value(); field(RELOAD[player].trig):clear_value()
      end
    end
    if r.grace > 0 then r.grace = r.grace - 1 end
    local down = input:seq_pressed(r.seq)
    local combo = down and input:seq_pressed(r.start_seq)
    if combo and not r.combo and recoil_on then set_recoil(player, false, "exit combo") end
    if r.combo and not combo then state[player] = nil end   -- re-evaluate from the ammo
    r.combo = combo
    if down and not r.down and not combo then hotd_reload(player) end
    r.down = down
  end
end

local started = false
local function setup()
  local rom = manager.machine.memory.regions[":maincpu"]
  local flash = "flash left on (unexpected ROM contents)"
  if rom and rom:read_u32(NOFLASH_AT) == ORIGINAL then
    rom:write_u32(NOFLASH_AT, RET)
    flash = "flash off"
  elseif rom and rom:read_u32(NOFLASH_AT) == RET then
    flash = "flash off (already patched)"
  end
  local input = manager.machine.input
  for player, spec in pairs(RELOAD) do
    if field(spec.x) and field(spec.trig) then
      rack[player] = { seq = input:seq_from_tokens(spec.rack), start_seq = input:seq_from_tokens(spec.start),
                       down = false, left = 0, grace = 0, combo = false }
    end
  end
  ports = gun_ports()
  load_a2()
  recoil_on = recoil_setting() ~= "disabled"
  log(string.format("start: %s, A2 %s, guns %s / %s, recoil %s", flash, a2 and table.concat(a2, " ") or "none (A1 fallback)",
    ports.p1 or "-", ports.p2 or "-", recoil_on and "managed" or "disabled in menu (left alone)"))
end

_G.hotd_sinden_sub = emu.add_machine_frame_notifier(function()
  if not started then setup(); started = true end
  rack_frame()
  if not recoil_on then return end
  local sh = manager.machine.memory.shares[":workram"]
  for player, off in pairs(AMMO) do
    local r = rack[player] or { grace = 0, combo = false }
    local loaded = sh:read_u8(off) > 0
    if not r.combo and state[player] ~= loaded and (loaded or r.grace == 0) then
      set_recoil(player, loaded, loaded and "clip loaded" or "clip empty")
      state[player] = loaded
    end
  end
end)
