-- debug wrapper: real plugin + autopilot (empties both clips, then stops shooting) + a
-- simulated rack for P1 at frame 2600 and P2 at 2700; ammo trace to /tmp/ammo.log
dofile("/userdata/system/sinden-recoil/hotd/hotd-sinden.lua")
local frame = 0
local function press(port, name, on)
  local f = manager.machine.ioport.ports[port].fields[name]
  if f then f:set_value(on and 1 or 0) end
end
_G.hotd_dbg_sub = emu.add_machine_frame_notifier(function()
  frame = frame + 1
  local c = (frame - 1400) % 60
  press(":IN0", "Coin 1", frame >= 1400 and frame < 1640 and c < 6)
  press(":IN0", "1 Player Start", frame >= 1700 and frame < 1706)
  press(":IN0", "2 Players Start", frame >= 1760 and frame < 1766)
  local cyc = (frame - 2000) % 45
  if frame >= 2000 and frame < 2550 then
    press(":IN1", "P1 Trigger", cyc < 4)
    press(":IN1", "P2 Trigger", cyc >= 20 and cyc < 24)
  elseif frame == 2550 then
    manager.machine.ioport.ports[":IN1"].fields["P1 Trigger"]:clear_value()
    manager.machine.ioport.ports[":IN1"].fields["P2 Trigger"]:clear_value()
  end
  if frame == 2600 then hotd_reload("p1") end
  if frame == 2700 then hotd_reload("p2") end
  if frame >= 1990 and frame % 15 == 0 then
    local sh = manager.machine.memory.shares[":workram"]
    local f = io.open("/tmp/ammo.log", "a")
    f:write(string.format("%d p1=%d p2=%d\n", frame, sh:read_u8(0x1ec41), sh:read_u8(0x1ed0d)))
    f:close()
  end
end)
