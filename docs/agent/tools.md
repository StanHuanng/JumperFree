# Mock agent tools v0.1

`jumperfree.agent` provides exactly four tools over one `MockAgentSession`.
The session loads `BoardProfile`, creates an empty `MockDevice`, connects one
`DeviceClient`, and creates one `ControlService`. Reuse that session for every
call in a conversation. State lives only in memory and is lost at process exit;
new sessions start empty. Close explicitly in `finally` or use `with`.

## Interface

Import `MockAgentSession`, `create_net`, `modify_net`, `delete_net`, `get_state`,
`TOOL_SCHEMAS`, and `TOOL_DESCRIPTIONS` from `jumperfree.agent`.
The Python functions take the session as their first argument. Bind it once so
the registered tools have these user-facing arguments:

| Tool | Arguments | ControlService operation |
| --- | --- | --- |
| `create_net` | `name: str, nodes: list[str]` | `create(net_id=name, nodes=nodes)` |
| `modify_net` | `name: str, nodes: list[str]` | `modify(net_id=name, nodes=nodes)` |
| `delete_net` | `name: str` | `delete(net_id=name)` |
| `get_state` | none | `query()` |

`name` is required and must contain a non-whitespace character. It is passed
unchanged as the exact net ID. `nodes` is a required array of strings containing
at least two distinct, exact profile node names. The packaged profile has
`Clip0` through `Clip15`, case-sensitive. Unknown, ambiguous, misspelled,
whitespace-padded, and physical-line names are errors. The tools never infer,
substitute, expand or edit the user's nodes. Semantic validation and routing
remain in ControlService and its existing router. No path or switch is selected
by the agent layer.

Every tool returns a JSON-serializable dictionary using
`ControlResult.as_dict()`:

- `ok`, `operation` (`create`, `modify`, `delete`, or `query`);
- `netlist`, `router`, `error`, `apply`;
- `device="MockDevice"`, `verified=false`.

Successful `netlist` contains confirmed session intent. Every failure has
`netlist=null`. A write's `router` is a candidate, and a query's `router` is
historical intent; neither is device confirmation. Error codes, messages,
details and apply results from ControlService are preserved. Shape validation
uses existing `INVALID_NET_ID` / `BAD_ARG` codes. Calls after close return
`SESSION_CLOSED` before shape validation, including for malformed arguments.
Connection/close failures raise the existing
structured exceptions; they occur outside the four tool calls.

## Confirmation and failure behavior

The [ControlService contract](../service/control.md) applies unchanged. Routing
and compilation failures never apply or change confirmed intent. Each valid
write delegates once; failed applies are never retried by this layer. Any failed
apply makes subsequent queries return `STATE_UNCONFIRMED` with `netlist=null`.
For `unresolved`, `timeout`, or `transport`, or any failed apply with
`state_changed="yes"` or `"unknown"`, subsequent writes return `CONTROL_BLOCKED`
before routing or execution. This includes `device_error` results whose state
change is unknown. Queries perform no device I/O.

No tool exposes or calls `acknowledge_unresolved`, clears nets/faults, reconnects,
or bypasses the service write gate or the client's stop. The session sets frame
retries to zero. DeviceClient's existing COMMIT-response-loss handling can issue
a transaction query; it does not resend COMMIT. This existing protocol behavior
is unchanged and does not provide an agent retry/recovery policy.

## Continuous Mock example

Run from `python/` with `PYTHONPATH=src`. This is the Python demo; no additional
CLI command is installed.

```python
from jumperfree.agent import (
    MockAgentSession, create_net, modify_net, delete_net, get_state,
)

session = MockAgentSession()
try:
    results = [
        create_net(session, "NET_A", ["Clip1", "Clip8"]),
        create_net(session, "NET_B", ["Clip2", "Clip9"]),
        modify_net(session, "NET_A", ["Clip1", "Clip10"]),
        get_state(session),
        delete_net(session, "NET_B"),
    ]
    for result in results:
        print(result)
    # The query retains A=[Clip1, Clip10], B=[Clip2, Clip9].
    # The final delete retains only A=[Clip1, Clip10].
    # Every result has device="MockDevice", verified=False.
finally:
    session.close()
```

## MCP registration suggestions

`TOOL_SCHEMAS` maps each tool name to an MCP-style definition containing
`name`, `description`, and `inputSchema`. `TOOL_DESCRIPTIONS` exposes the same
descriptions separately. Required fields and argument types are in the schemas;
the distinct-node and exact-profile-name rules are also checked at runtime by
the existing control/router layer. No MCP server or SDK is included.

For a future MCP host, create one session for its owner and bind the functions:

```python
from functools import partial
from jumperfree.agent import (
    MockAgentSession, TOOL_SCHEMAS,
    create_net, modify_net, delete_net, get_state,
)

with MockAgentSession() as session:
    handlers = {tool.__name__: partial(tool, session) for tool in (
        create_net, modify_net, delete_net, get_state,
    )}
    definitions = list(TOOL_SCHEMAS.values())
    # Register definitions and handlers in the host; keep its serving loop
    # inside this context. Serialize calls for this single-owner session.
```

Suggested agent instructions: require explicit net names and exact profile
nodes; request clarification for ambiguous intent; pass arguments unchanged;
present structured failures unchanged. Treat `netlist=null` as unconfirmed, and
never report candidate/history as current wiring. Stop writes on
`CONTROL_BLOCKED`; never acknowledge, retry, clear, or recover automatically.
Always present Mock results as `verified=false`.

## Limits

Mock only, single owner, sequential calls, initially empty device, no database
or persistence, no external-change monitoring or reconciliation. There is no
natural-language parser, LLM SDK, HTTP/MCP server, alternate adapter, hardware,
STM32 or firmware integration. Hardware wiring, timing and electrical behavior
remain unverified. The layer does not construct JF2 frames, CRCs, router paths,
Y channels, crosspoints or physical plans.
