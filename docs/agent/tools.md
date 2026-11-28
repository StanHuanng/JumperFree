# JumperFree Host: Agent tools and MCP server

JumperFree Host is a local control program for local AI Agents. It is the
installable Python package `jumperfree`. The Agent client starts one Host
process at the start of a conversation (`jumperfree-mcp`, stdio MCP). The process
runs during the conversation and exits when the conversation ends.

The Host is not a web console, not a wiring GUI, not a boot-time service, and it
holds no language model. Claude Code, Cursor and Codex use the same command; see
[install.md](install.md). Agent rules: [`SKILL.md`](../../python/src/jumperfree/agent/SKILL.md).

## Layers

| Layer | Does | Never does |
| --- | --- | --- |
| Agent | Names nets and nodes; follows plan → user yes → LED guide → wait → execute | Choose crosspoints or BUS lines, write switch registers |
| Host (`jumperfree.agent` → `service` → `board`/`routing` → `host`) | Validates nodes, routes, compiles the SwitchPlan, sends JF2, keeps intent in memory | Persist state, run a model, serve HTTP |
| Board firmware | Executes JF2 safely: all switches open at boot, on lease expiry, on faults; emergency stop resets the MCU | Accept switch commands outside JF2 transactions |

One process = one session = one owner. Tool calls run strictly in order. Process
exit loses the session; there is no database.

## Session lifecycle

1. **Lazy connect.** The first tool call opens the board: `--port COMx`, or a search
   over USB-UART bridges (WCH VID 0x1A86) that sends `JF2 INFO` and accepts a JF2
   reply. Other USB serial devices are never written to. A port held by another
   program returns `PORT_BUSY`; no board returns `DEVICE_NOT_FOUND`. Until the
   first connect succeeds, each tool call tries again.
2. **Board detection.** `INFO board=` selects the packaged profile: `mini` →
   JumperFree mini (`boards/mini`), `v01` → v0.1 (`boards/v01`). `--board` makes a
   different board an error (`BOARD_MISMATCH`).
3. **Clean start.** Latched faults (for example `LEASE_EXPIRED` from a Host that died)
   are cleared with `FAULT CLEAR`; an active fault stops the start (`DEVICE_FAULT`).
   `HELLO` arms the board. Switches left closed by an earlier program are opened
   (`SW ALLOPEN`, then `HELLO`). Both actions appear in `describe_board.startup_notes`.
4. **Lease and heartbeat.** The Host sends `LEASE 5000` and then `PING` every 1 s.
   The heartbeat takes the same lock as tool calls, so it never interleaves with a
   transaction. If the Host stops for more than 5 s, the firmware opens every switch.
5. **Lost session.** A heartbeat that sees a fault mask (lease expiry), a lower uptime
   (MCU reset or emergency stop) or no reply ends the session: every later call
   returns `SESSION_LOST` with the cause. The Host never re-arms on its own; restart
   the Host (new conversation, or reconnect the MCP server).
6. **Exit.** On stdin EOF, SIGTERM/SIGBREAK or Ctrl-C the Host turns all LEDs off,
   sends `SW ALLOPEN` and closes the port. If the process is killed without cleanup,
   the lease opens the switches within 5 s (the LEDs then keep their last colors).

## Tools

| Tool | Arguments | Device I/O | Result |
| --- | --- | --- | --- |
| `describe_board` | – | connect only | board name, clips (row, column), service nodes and limits, capacity, `startup_notes` |
| `get_state` | – | none | confirmed netlist, or `STATE_UNCONFIRMED` after a failed change |
| `plan_net_change` | `action` create/modify/delete, `name`, `nodes` (not for delete) | none | router candidate, `plan` summary (breaks, makes, disturbed nets), `plan_id` |
| `guide_leds` | optional `plan_id` | one `LED BATCH` | planned (with `plan_id`) or confirmed netlist on the LEDs; `led.nets[].nodes[].where`, `added_nodes`, `removed_nodes` |
| `execute_plan` | `plan_id` | one JF2 transaction | confirmed netlist and the device's apply report |
| `all_open` | – | `SW ALLOPEN`, `HELLO`, `LEASE` | empty netlist; the session continues |

Every result is a JSON object with `ok`, `operation`, `netlist`, `router`, `error`,
`apply`, `device` and `verified=false`. Error codes pass through unchanged.

### Order is enforced by the Host

- `plan_net_change` issues `plan_id` = hash of (action, name, nodes, state version).
- `guide_leds(plan_id)` marks that plan as shown.
- `execute_plan(plan_id)` runs only a known plan of the current state version that
  was shown: otherwise `PLAN_UNKNOWN`, `PLAN_STALE` or `PLAN_NOT_GUIDED`, with no
  device I/O. Any change (or `all_open`) makes every older plan invalid.
- The user's confirmation itself cannot be checked by the Host; the Skill and the
  MCP `instructions` require it.

### Node rules (JumperFree mini)

- Clips `A1`..`A24` (row far from the user) and `B1`..`B24` (row near the user),
  column 1..24 from the left. LED index = FLIP number (see `boards/mini/nodes.json`).
- `TEST_SRC` = U307 Y6 (PB8 via 1.5 kOhm, signal level ~3.3 V). `SVC_GND` = U307 Y7.
  A net with both is refused (`SERVICE_SHORT`).
- U307 Y0..Y5 carry measurement paths. They are internal nodes `~U307.Yn`: the router
  never uses them and a net that names them is refused (`NODE_RESERVED`).
- One net = one BUS (X line shared by all chips) and one JF2 tag; each clip in the net
  closes one switch on its own chip. At most 15 nets (tags 1..15).
- LED color = tag, so a net keeps its color while other nets change.

## MCP server

`jumperfree-mcp [--port COMx | --mock] [--board mini|v01]` (also
`python -m jumperfree.agent.mcp_server`). Hand-written newline-delimited JSON-RPC 2.0
over stdio: `initialize` (protocol versions 2025-06-18, 2025-03-26, 2024-11-05;
the result carries `instructions` with the fixed order), `ping`, `tools/list`,
`tools/call`. Notifications are ignored. Arguments must match the schema exactly
(missing or extra keys → `-32602`). `tools/call` returns the tool dict as
`structuredContent` and as JSON text; `isError = not ok`. Logs go to stderr.

`--mock` runs an in-memory JumperFree mini (`MockDevice` with the mini geometry) for
tests and demos without hardware.

## Limits

- `verified=false` always: an executed plan is the firmware's report, not a
  measurement of electrical contact.
- Matrix paths are about 100 Ohm; at most 15 mA per switch; signals within VEE..VDD.
- One BUS per net: power nets for larger loads (parallel BUS) are not supported yet.
- No measurement tools yet (the firmware `meas`/`probe`/`scope` commands are console
  commands, not JF2).

## Hardware check

`python/hil/mini_c3_aht.py` drives the installed `jumperfree-mcp` like an Agent on a
real JumperFree mini with an ESP32-C3 (jf-c3-env firmware) and an AHT20 on the
board. The C3's I2C scan is independent evidence that the matrix really connects
the sensor. It covers routing, `PORT_BUSY`, delete/re-create, idle lease, normal
exit and a killed Host (lease expiry).
