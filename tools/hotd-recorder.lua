-- House of the Dead (MAME hotdo) memory recorder: find the ammo counters and the shot flash.
-- Loaded via /userdata/system/configs/mame/hotdo.ini (autoboot_script). Writes to
-- /userdata/system/hotd-rec/: info.txt (inputs + memory shares), then on every trigger pull
-- of either gun a snapshot of all RAM shares (<n>_<player>_<frame>.bin) plus one 3 frames
-- later (to catch the flash). Remove the .ini line when done.
local OUT = "/userdata/system/hotd-rec/"
os.execute("mkdir -p " .. OUT)

local function log(s)
  local f = io.open(OUT .. "info.txt", "a"); f:write(s .. "\n"); f:close()
end

local shares, triggers = {}, {}
local started = false
local count, frame, pending = 0, 0, {}

local function dump(name)
  local f = io.open(OUT .. name .. ".bin", "wb")
  for _, sh in ipairs(shares) do
    local parts = {}
    for off = 0, sh.size - 4, 4 do
      parts[#parts + 1] = string.pack("<I4", sh.share:read_u32(off))
    end
    f:write(table.concat(parts))
  end
  f:close()
end

-- HOTD_NOFLASH=<hex workram offset>: force that byte to 0 on every CPU write (test which
-- byte triggers the white gun flash). Workram sits at 0x500000 in the i960 address space.
local WORK = 0x500000
local function install_noflash()
  local hex = os.getenv("HOTD_NOFLASH")
  if not hex then return end
  local sp = manager.machine.devices[":maincpu"].spaces["program"]
  local sh = manager.machine.memory.shares[":workram"]
  log(string.format("check: share[0x1ec41]=%d space[0x%x]=%d", sh:read_u8(0x1ec41), WORK + 0x1ec41, sp:read_u8(WORK + 0x1ec41)))
  for h in hex:gmatch("[^,]+") do
    local a = WORK + tonumber(h, 16)
    local word, shift = a & ~3, (a & 3) * 8
    _G["hotd_tap_" .. h] = sp:install_write_tap(word, word + 3, "noflash" .. h, function(offset, data, mask)
      if (mask >> shift) & 0xff ~= 0 then return data & ~(0xff << shift) end
    end)
    log("noflash tap on workram+0x" .. h)
  end
end

local function setup()
  install_noflash()
  log("== shares (dumped in this order, concatenated)")
  local total = 0
  for tag, sh in pairs(manager.machine.memory.shares) do
    if sh.size >= 1024 and sh.size <= 8 * 1024 * 1024
        and not tag:find("texture") and not tag:find("sound") and not tag:find("copro") then
      shares[#shares + 1] = {tag = tag, share = sh, size = sh.size}
    end
  end
  table.sort(shares, function(a, b) return a.tag < b.tag end)
  for _, sh in ipairs(shares) do
    log(string.format("share %s size %d offset_in_dump %d", sh.tag, sh.size, total))
    total = total + sh.size
  end
  log("== inputs")
  for ptag, port in pairs(manager.machine.ioport.ports) do
    for fname, field in pairs(port.fields) do
      log(string.format("port %s field %s mask %x def %x", ptag, fname, field.mask, field.defvalue))
      local n = fname:lower()
      if n:find("button 1") or n:find("trigger") then
        local player = fname:match("^P(%d)") or "?"
        triggers[#triggers + 1] = {port = port, field = field, player = player, down = false}
      end
    end
  end
  for _, t in ipairs(triggers) do log("trigger watch: P" .. t.player .. " " .. t.field.name) end
end

-- HOTD_AUTO=1: play by itself (coin, 2P start, both guns shoot the screen centre every
-- 45 frames) and take a screenshot 0-4 frames after each P1 shot (finds the flash frames).
local AUTO = os.getenv("HOTD_AUTO") == "1"
local function press(port, name, on)
  local f = manager.machine.ioport.ports[port].fields[name]
  if f then f:set_value(on and 1 or 0) end
end

_G.hotd_rec_sub = emu.add_machine_frame_notifier(function()
  if not started then setup(); started = true end
  frame = frame + 1
  if AUTO then
    -- 2 credits per player: 4 coins once attract runs, then both starts
    local c = (frame - 1400) % 60
    press(":IN0", "Coin 1", frame >= 1400 and frame < 1640 and c < 6)
    press(":IN0", "1 Player Start", frame >= 1700 and frame < 1706)
    press(":IN0", "2 Players Start", frame >= 1760 and frame < 1766)
    local S = 2000
    local cyc = (frame - S) % 45
    press(":IN1", "P1 Trigger", frame >= S and cyc < 4)
    press(":IN1", "P2 Trigger", frame >= S and cyc >= 20 and cyc < 24)
    -- HOTD_FLASH=1: P1 shots 6-14 (gameplay; 6-10 with ammo, 11+ empty): screenshot +
    -- memory snapshot on each of the 9 frames after the trigger
    local shot = (frame - S) // 45 + 1
    if os.getenv("HOTD_FLASH") == "1" and frame >= S and shot >= 6 and shot <= 14 and cyc <= 8 then
      manager.machine.video:snapshot()
      dump(string.format("flash_s%02d_f%d_%07d", shot, cyc, frame))
    end
  end
  for _, t in ipairs(triggers) do
    local down = (t.port:read() & t.field.mask) ~= (t.field.defvalue & t.field.mask)
    if down and not t.down then
      count = count + 1
      local name = string.format("%04d_p%s_%07d", count, t.player, frame)
      dump(name)
      pending[#pending + 1] = {at = frame + 3, name = name .. "_after"}
      log(string.format("shot %d P%s frame %d", count, t.player, frame))
    end
    t.down = down
  end
  for i = #pending, 1, -1 do
    if frame >= pending[i].at then dump(pending[i].name); table.remove(pending, i) end
  end
end)
