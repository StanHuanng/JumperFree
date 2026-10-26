"""Bind these four functions to one MockAgentSession for tool registration."""

from __future__ import annotations

from .session import MockAgentSession


TOOL_DESCRIPTIONS = {
    "create_net": (
        "Create a named net on MockDevice. Require a nonblank name and an array "
        "with at least two distinct exact profile node names. Never infer, trim, "
        "replace or expand nodes. Mock results always have verified=false."
    ),
    "modify_net": (
        "Replace the nodes of an existing named net on MockDevice, preserving "
        "other nets. Require a nonblank name and at least two distinct exact "
        "profile nodes. Do not guess ambiguous nodes or rewrite user intent."
    ),
    "delete_net": "Delete only the net with this exact nonblank name on MockDevice.",
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


TOOL_SCHEMAS = {
    name: {"name": name, "description": TOOL_DESCRIPTIONS[name], "inputSchema": schema}
    for name, schema in (
        ("create_net", _input_schema(nodes=True)),
        ("modify_net", _input_schema(nodes=True)),
        ("delete_net", _input_schema()),
        ("get_state", _input_schema(name=False)),
    )
}


def create_net(session: MockAgentSession, name: str, nodes: list[str]) -> dict[str, object]:
    """Create name as ControlService's net_id; do not change supplied nodes."""
    return session._invoke("create", name, nodes)


def modify_net(session: MockAgentSession, name: str, nodes: list[str]) -> dict[str, object]:
    """Replace one net through a single ControlService modify operation."""
    return session._invoke("modify", name, nodes)


def delete_net(session: MockAgentSession, name: str) -> dict[str, object]:
    """Delete exactly name through ControlService."""
    return session._invoke("delete", name)


def get_state(session: MockAgentSession) -> dict[str, object]:
    """Return confirmed intent or the service's unconfirmed-state error."""
    return session._invoke("query")
