"""Bind these functions to one AgentSession for tool registration."""

from __future__ import annotations

from .session import AgentSession


TOOL_DESCRIPTIONS = {
    "create_net": (
        "Create a named net on the session device. Require a nonblank name and an array "
        "with at least two distinct exact profile node names. Never infer, trim, "
        "replace or expand nodes. Mock results always have verified=false."
    ),
    "modify_net": (
        "Replace the nodes of an existing named net on the session device, preserving "
        "other nets. Require a nonblank name and at least two distinct exact "
        "profile nodes. Do not guess ambiguous nodes or rewrite user intent."
    ),
    "delete_net": "Delete only the net with this exact nonblank name on the session device.",
    "plan_net_change": (
        "Read-only dry run of create/modify/delete on a copy of confirmed intent: "
        "routes and compiles, returns candidate router, break/make counts and occupied "
        "nodes. No device I/O; never changes netlist, epoch or device. A plan is a "
        "software result, not an applied connection. Errors match the direct tool. "
        "verified is always false."
    ),
    "guide_leds": (
        "Light the Clip LEDs with one LED BATCH: each net gets one color, all other clips "
        "are sent black (n is always 16). With no arguments it shows the confirmed netlist; "
        "with action/name/nodes (as plan_net_change) it shows the netlist that change would "
        "produce, before it is executed (the change itself is not applied). Needs a confirmed "
        "state and an ARMED device; SAFE_MODE is returned as is, never auto-armed. No "
        "switch change. verified is always false."
    ),
    "all_open": (
        "Open every switch on the device (SW ALLOPEN). Works in any device state but is "
        "not a recovery tool: afterwards the session state is unconfirmed (netlist=null) "
        "and all writes, plans and guides are refused until a new session is created."
    ),
    "get_state": (
        "Query session intent without device I/O. Only netlist is confirmed "
        "intent; router is candidate/history, never device confirmation. After "
        "failed apply, netlist is null and state is unconfirmed. Hardware is unverified."
    ),
}


def _input_schema(*, nodes: bool = False, name: bool = True) -> dict[str, object]:
    properties: dict[str, object] = {}
    if name:
        properties["name"] = {
            "type": "string", "minLength": 1, "pattern": r"\S",
            "description": "Exact net ID; must contain a non-whitespace character.",
        }
    if nodes:
        properties["nodes"] = {
            "type": "array", "minItems": 2, "items": {"type": "string"},
            "description": (
                "At least two distinct, case-sensitive node names from the session's "
                "BoardProfile. Unknown or ambiguous names are errors; never substitute."
            ),
        }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _plan_schema() -> dict[str, object]:
    schema = _input_schema(nodes=True)
    schema["properties"] = {"action": {
        "type": "string", "enum": ["create", "modify", "delete"],
        "description": "Which change to plan; nodes is ignored for delete and may be omitted.",
    }, **schema["properties"]}
    schema["required"] = ["action", "name"]
    return schema


def _guide_schema() -> dict[str, object]:
    schema = _plan_schema()
    schema["required"] = []  # action and name go together; a lone one is BAD_ARG/INVALID_NET_ID
    schema["properties"]["action"] = {**schema["properties"]["action"], "description": (
        "Optional. With name (and nodes unless delete), previews that planned change's LEDs.")}
    return schema


TOOL_SCHEMAS = {
    name: {"name": name, "description": TOOL_DESCRIPTIONS[name], "inputSchema": schema}
    for name, schema in (
        ("create_net", _input_schema(nodes=True)),
        ("modify_net", _input_schema(nodes=True)),
        ("delete_net", _input_schema()),
        ("plan_net_change", _plan_schema()),
        ("guide_leds", _guide_schema()),
        ("all_open", _input_schema(name=False)),
        ("get_state", _input_schema(name=False)),
    )
}


def create_net(session: AgentSession, name: str, nodes: list[str]) -> dict[str, object]:
    """Create name as ControlService's net_id; do not change supplied nodes."""
    return session._invoke("create", name, nodes)


def modify_net(session: AgentSession, name: str, nodes: list[str]) -> dict[str, object]:
    """Replace one net through a single ControlService modify operation."""
    return session._invoke("modify", name, nodes)


def delete_net(session: AgentSession, name: str) -> dict[str, object]:
    """Delete exactly name through ControlService."""
    return session._invoke("delete", name)


def plan_net_change(session: AgentSession, action: str, name: str,
                    nodes: list[str] | None = None) -> dict[str, object]:
    """Dry-run create/modify/delete; nodes is required for create and modify only."""
    return session._invoke("plan", action, name, nodes)


def get_state(session: AgentSession) -> dict[str, object]:
    """Return confirmed intent or the service's unconfirmed-state error."""
    return session._invoke("query")


def guide_leds(session: AgentSession, action: str | None = None, name: str | None = None,
               nodes: list[str] | None = None) -> dict[str, object]:
    """Send one LED BATCH for the confirmed netlist, or for a planned change if
    action/name are given; no arming, no switch change."""
    if action is None and name is None and nodes is None:
        return session._invoke("guide_leds")
    return session._invoke("guide_leds", action, name, nodes)


def all_open(session: AgentSession) -> dict[str, object]:
    """Open all switches; leaves the session unconfirmed and write-refusing."""
    return session._invoke("all_open")
