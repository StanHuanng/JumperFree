"""The seven Agent tools. Each takes the session first; MCP binds it once per process.

The Agent only names nets and nodes. It never picks crosspoints, BUS lines or
switch registers: the router and the board firmware do that.
"""

from __future__ import annotations

from typing import Any

from .session import AgentSession

INSTRUCTIONS = """\
JumperFree Host: you control a jumper-free breadboard. You submit net intent only
(a net name and its nodes). The Host routes it; the board firmware switches it.

Fixed order for every change:
1. plan_net_change (read-only) -> returns plan_id.
2. Show the plan to the user (net, nodes) and wait for an explicit yes.
3. guide_leds(plan_id): the board lights the clips. Tell the user, part by part,
   which clips to use: row A = far side, row B = near side, column 1..24 from the left,
   pin orientation, and the power order (board first, then the user circuit).
4. Wait until the user says the parts are in place.
5. execute_plan(plan_id).
Never skip a step. Never invent node names: call describe_board. If a result has
ok=false, quote error.code and stop; do not retry with changed arguments.
SESSION_LOST means the board opened every switch (lease, fault, reset or emergency
stop): tell the user and stop; the Host must be restarted.
For a supply or ground net (VCC, 3V3, GND of a module) pass current_ma, the load's
current; the Host then uses parallel paths. Signal nets need no current_ma.
measure_voltage reads the voltage on one clip (it never changes the netlist). Use it
to check a supply net after execute_plan, e.g. 3V3 reads about 3.3 V.
verified=false always: a successful execute is the board's report, not a
measurement of real electrical connection.
"""

TOOL_DESCRIPTIONS = {
    "describe_board": (
        "Read-only. Connect to the board if needed and return its name, every node name "
        "(clips with row A/B and column, service nodes with their limits), capacity and "
        "electrical limits. Call this first; use only node names it returns."),
    "get_state": (
        "Read-only, no device I/O. Return the confirmed netlist of this session. After a "
        "failed change the state is unconfirmed (netlist=null)."),
    "plan_net_change": (
        "Read-only dry run of create/modify/delete for one net: routes it and returns the "
        "switch changes, affected nets and a plan_id. Changes nothing on the board. Show the "
        "plan to the user and get a yes before guide_leds. For supply/ground nets pass "
        "current_ma: the Host routes ceil(current_ma/15) BUS paths in parallel."),
    "guide_leds": (
        "Light the board LEDs. With plan_id: show the netlist that plan would produce (one "
        "color per net, other clips dark) and return each net's clips with row and column, "
        "plus added_nodes the user must plug. Without plan_id: show the confirmed netlist. "
        "Never changes a switch."),
    "execute_plan": (
        "Apply a plan: only a plan_id from plan_net_change for the current state that was "
        "shown with guide_leds. Call it only after the user said the parts are in place. "
        "Errors: PLAN_UNKNOWN, PLAN_STALE (state changed, plan again), PLAN_NOT_GUIDED."),
    "all_open": (
        "Open every switch on the board and turn the LEDs off (the user's 'disconnect "
        "everything'). The session continues with an empty netlist."),
    "measure_voltage": (
        "Read the voltage on one clip (volts; range about -7.4..+5.5 V, input about 1 MOhm). "
        "Uses the clip's own net path, or for a clip in no net a free BUS for the reading only; "
        "every switch is restored and the netlist does not change. An unconnected clip reads "
        "about 2.5 V. window_ms > 0 also returns min/max over that time (signal activity)."),
}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


_PLAN_ID = {"type": "string", "pattern": "^p-[0-9a-f]{10}$",
            "description": "plan_id returned by plan_net_change."}

TOOL_SCHEMAS = {
    name: {"name": name, "description": TOOL_DESCRIPTIONS[name], "inputSchema": schema}
    for name, schema in (
        ("describe_board", _schema({}, [])),
        ("get_state", _schema({}, [])),
        ("plan_net_change", _schema({
            "action": {"type": "string", "enum": ["create", "modify", "delete"],
                       "description": "create a new net, modify (replace the nodes of) a net, or delete it."},
            "name": {"type": "string", "minLength": 1, "pattern": r"\S",
                     "description": "Net name, e.g. SDA or LED1. Use the user's name for it."},
            "nodes": {"type": "array", "minItems": 2, "items": {"type": "string"},
                      "description": ("Exact node names from describe_board (e.g. A3, B12, SVC_GND). "
                                      "Required for create and modify; omit for delete.")},
            "current_ma": {"type": "integer", "minimum": 1, "maximum": 100,
                           "description": ("Supply/ground nets only: the load's continuous current in mA. "
                                           "Omit for signal nets.")},
        }, ["action", "name"])),
        ("guide_leds", _schema({"plan_id": _PLAN_ID}, [])),
        ("execute_plan", _schema({"plan_id": _PLAN_ID}, ["plan_id"])),
        ("all_open", _schema({}, [])),
        ("measure_voltage", _schema({
            "node": {"type": "string", "description": "A clip name from describe_board, e.g. A3."},
            "window_ms": {"type": "integer", "minimum": 0, "maximum": 1000,
                          "description": "0 (default): one averaged reading. >0: also min/max over this time."},
        }, ["node"])),
    )
}


def describe_board(session: AgentSession) -> dict[str, Any]:
    return session.describe_board()


def get_state(session: AgentSession) -> dict[str, Any]:
    return session.get_state()


def plan_net_change(session: AgentSession, action: str, name: str,
                    nodes: list[str] | None = None, current_ma: int | None = None) -> dict[str, Any]:
    return session.plan(action, name, nodes, current_ma)


def guide_leds(session: AgentSession, plan_id: str | None = None) -> dict[str, Any]:
    return session.guide(plan_id)


def execute_plan(session: AgentSession, plan_id: str) -> dict[str, Any]:
    return session.execute(plan_id)


def all_open(session: AgentSession) -> dict[str, Any]:
    return session.all_open()


def measure_voltage(session: AgentSession, node: str, window_ms: int = 0) -> dict[str, Any]:
    return session.measure(node, window_ms)
