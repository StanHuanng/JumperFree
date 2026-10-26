---
name: jumperfree
description: Plan and execute breadboard net connections through the JumperFree MCP tools (Mock/software evidence only)
---

# JumperFree Skill

You submit connection intent (Nets of nodes) through seven tools. You never pick
crosspoints, pins or switches; the router and controller do. Tool reference:
`docs/agent/tools.md`. Every result carries `verified=false`.

## Node naming

- Only exact, case-sensitive `Clip0` … `Clip15` are valid. `clip0`, `Clip 0`, `Clip16`,
  `CLIP0`, `J1` are not.
- If the user says "node 1", "1号节点" or any bare number, you cannot tell whether it means
  `Clip0` or `Clip1`. **Ask which numbering they use. Never guess.**
- Do not infer pins, expand, merge, trim or substitute nodes. If a name is unknown, report
  the tool error and ask.
- Net names: use what the user gave; if none, ask.

## Fixed workflow

1. **Plan**: `plan_net_change` (action create/modify/delete). Read-only, no device I/O.
2. **Show** the user each Net and its nodes (and break/make counts, disturbed nets).
   Wait for explicit confirmation.
3. **Guide**: `guide_leds` with the *same* `action`/`name`/`nodes` as the confirmed plan
   lights the Clips of the netlist that change would produce, before anything is
   executed (no switch change; see "LED timing" below).
4. Wait for the user to confirm the parts are installed.
5. **Execute**: `create_net` / `modify_net` / `delete_net` with the same arguments that
   were planned and confirmed.
6. **Report** (see wording below).

Changing a net: re-plan with `modify_net`'s arguments, show, confirm again. Never call
`modify_net` / `delete_net` directly without a fresh confirmed plan.
`all_open` is the user's "disconnect everything": call it when asked.

LED timing: `guide_leds` with plan arguments previews the planned netlist (all 16 clips
are sent; others go dark) without applying it; with no arguments it colors the confirmed
netlist. The LEDs are guidance only, never evidence of connectivity. If the device is
not ARMED the `SAFE_MODE` error is returned; do not arm or reconnect.

## Stop conditions

- `CONTROL_BLOCKED`: stop all writes immediately. Quote the error unchanged. Do not
  retry, clear, reconnect or attempt recovery. Wait for the user.
- `STATE_UNCONFIRMED` / `SESSION_REESTABLISH_REQUIRED`: report as is. After `all_open`
  the session accepts no further writes; a new session is needed.
- `SAFE_MODE` from `guide_leds`: report; do not arm or reconnect.
- Any other error: show code and message verbatim, do not retry, do not "fix" arguments.

## Reporting wording

Keep three layers separate:

1. **Software plan result**: "the router found a candidate route" (`plan_net_change`).
2. **Mock / controller-reported execution status**: "the Mock device reported the write
   succeeded" (`create_net` etc., `apply`).
3. **Real electrical connectivity**: cannot be measured. **Never claim it.**

Also: `netlist=null` means state is unconfirmed, say so. `router` is a candidate or
history, never "the current wiring". State `verified=false` in every report. Do not say
"connected", "wired" or "verified" without the qualifier "reported by Mock/controller".
