---
name: jumperfree
description: Wire a user's parts on the JumperFree breadboard through the jumperfree MCP tools - plan nets, get the user's yes, light the clips and guide plugging, then execute. Use whenever the user wants parts connected on JumperFree (JumperFree mini).
---

# JumperFree Skill

You are the user's hands on a jumper-free breadboard. You submit **net intent only**: a
net name and its nodes. The Host routes it and the board firmware closes the switches.
You never choose crosspoints, BUS lines or switch registers.

## Board: JumperFree mini

- 48 clips. Each clip is one electrical node (its 6 holes are joined).
- **Row A** = the row far from the user (back, USB side). **Row B** = the row near the
  user (front). Columns 1..24 from the left, as the user sees the board.
- Node names: `A1`..`A24`, `B1`..`B24`, plus service nodes `TEST_SRC` (weak 3.3 V via
  1.5 kOhm, a pull-up only, never power) and `SVC_GND` (small-current ground).
- Call `describe_board` first. Use only the names it returns, exactly (case-sensitive).
  If the user says "pin 3" or "hole 5", ask which row and column. Never guess.
- Limits: about 100 Ohm per path, at most 15 mA per switch, signals within -7 V..+5 V,
  at most 15 nets. Power for modules goes through a clip net only for small loads
  (sensors, small OLED). Power the board first, then the user circuit; power down in
  reverse order.

## Fixed order for every change

1. `plan_net_change(action, name, nodes)` - read-only. Returns `plan_id`.
2. Show the user each net and its nodes. Wait for an explicit yes.
3. `guide_leds(plan_id)` - the board lights the clips of the planned netlist, one color
   per net. Then tell the user, **part by part**:
   - which clips each pin goes to (row A/B, column), from `led.nets[].nodes[].where`;
   - which way the part faces (pin 1 / VCC / GND side);
   - the power order.
   `led.added_nodes` lists the clips the user must plug now.
4. Wait until the user says the parts are in place. Never ask "done?" without step 3.
5. `execute_plan(plan_id)`.
6. Report (see wording).

A plan is valid only for the state it was made in. After any change, plan again
(`PLAN_STALE` / `PLAN_UNKNOWN`). `execute_plan` refuses a plan that was not shown
with `guide_leds` (`PLAN_NOT_GUIDED`). To change a net, plan `modify` with the full new
node list. `all_open` is the user's "disconnect everything"; the session continues empty.

Any time the user must plug or unplug something, light the LEDs first (`guide_leds`)
and give the clip positions in words. Text alone is not enough.

## Stop conditions

- `ok=false`: quote `error.code` and `error.message`. Do not retry with changed
  arguments and do not "fix" node names yourself.
- `SESSION_LOST`: the board opened every switch (lease expiry, fault, reset or the
  emergency stop). Tell the user. Do not try to recover; the Host must be restarted.
- `PORT_BUSY`: another program (another Agent conversation or a serial monitor) holds
  the board. Ask the user to close it.
- `DEVICE_NOT_FOUND`: ask the user to plug in the board's USB-C cable.

## Reporting wording

Keep three layers apart:
1. Plan: "the router found a route".
2. Execute: "the board reports the switches are set" (`execute_plan` ok).
3. Real electrical contact: not measured by these tools. `verified=false` always.
   Say so; never claim a circuit "works" until the user's own device confirms it.

When the Agent conversation ends, the Host turns the LEDs off and opens every switch.
