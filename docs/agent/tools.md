# Agent tools and MCP server

`jumperfree.agent` provides seven tools over one `AgentSession` (`MockAgentSession`
for mock). They are served over stdio by `jumperfree.agent.mcp_server`; agent behavior
rules are in [`SKILL.md`](../../python/src/jumperfree/agent/SKILL.md). The session loads `BoardProfile`, creates an empty `MockDevice`, connects one
`DeviceClient`, and creates one `ControlService`. Reuse that session for every
call in a conversation. State lives only in memory and is lost at process exit;
new sessions start empty. Close explicitly in `finally` or use `with`.

## Session factory

`open_session("mock")` returns a `MockAgentSession` (fresh `MockDevice`).
`open_session("serial", port, baudrate=None, timeout=None)` wraps the existing
`SerialTransport` in an `AgentSession`; pyserial stays optional and is imported
only on this path (missing → `SERIAL_UNAVAILABLE`; mock is unaffected). Tests
inject a fake transport via `serial_factory`. A serial session requires an
initially empty device. All results are software/Mock-level evidence only.

## Interface

Import `open_session`, `AgentSession`, `MockAgentSession`, `create_net`, `modify_net`, `delete_net`, `get_state`, `plan_net_change`,
`guide_leds`, `all_open`, `TOOL_SCHEMAS`, and `TOOL_DESCRIPTIONS` from `jumperfree.agent`.
The Python functions take the session as their first argument. Bind it once so
the registered tools have these user-facing arguments:

| Tool | Arguments | ControlService operation |
| --- | --- | --- |
| `create_net` | `name: str, nodes: list[str]` | `create(net_id=name, nodes=nodes)` |
| `modify_net` | `name: str, nodes: list[str]` | `modify(net_id=name, nodes=nodes)` |
| `delete_net` | `name: str` | `delete(net_id=name)` |
| `plan_net_change` | `action: "create"\|"modify"\|"delete", name: str, nodes: list[str]` (omit `nodes` for delete) | `plan(action, net_id, nodes)` |
| `guide_leds` | none | `guide_leds()` |
| `all_open` | none | `all_open()` |
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
- `device`: `"MockDevice"` for mock sessions, `"serial:<port>"` for serial sessions; `verified=false` always.

Successful `netlist` contains confirmed session intent. Every failure has
`netlist=null`. A write's `router` is a candidate, and a query's `router` is
historical intent; neither is device confirmation. Error codes, messages,
details and apply results from ControlService are preserved. Shape validation
uses existing `INVALID_NET_ID` / `BAD_ARG` codes. Calls after close return
`SESSION_CLOSED` before shape validation, including for malformed arguments.
Connection/close failures raise the existing
structured exceptions; they occur outside the tool calls.

## Planning vs. execution

`plan_net_change` is a read-only dry run; `create_net`, `modify_net` and
`delete_net` are the execution tools and are unchanged. The three intents are
expressed by `action` (`create`, `modify`, `delete`) with the same `name` and
`nodes` arguments as the matching execution tool (`nodes` only for create and
modify). It deep-copies the confirmed intent, runs the router and `compile_plan`,
and returns `operation="plan"` with:

- `router`: the candidate routes (software result, not current wiring);
- `plan`: `action`, `net`, `base_epoch`, `breaks` / `makes` (switch operations to
  open / close), `disturbed_nets`, `occupied_nodes` (nodes in the candidate
  netlist) and `occupancy`;
- `netlist=null`, `apply=null`, `verified=false`.

It performs no device I/O (no `TXN BEGIN`, no LED), and leaves confirmed intent,
epoch, tags and the device untouched. Failures return the same error code,
message, details and `router` as the direct tool would for the same input
(unknown node, duplicate/unknown net, capacity, shape errors). `SESSION_CLOSED`
and `CONTROL_BLOCKED` are returned in the same precedence as for writes: a
blocked session cannot be planned against, because its confirmed intent is no
longer trustworthy. An unknown `action` returns `BAD_ARG`. A successful plan only
shows what execution would attempt from the current confirmed state; execution may
still fail at the device, and a plan is never evidence of connectivity.

## LED guidance and all-open

`guide_leds` colors the Clip LEDs from the **confirmed** netlist (no plan input) with
one `LED BATCH`. It always sends all 16 clips: clips in a net get that net's palette
color, every other clip is sent `000000`, so stale guidance is cleared and `n` is always
16 (an empty netlist blanks all LEDs). The result carries `led={count, clips}`. It does
no switch change. If the device is not ARMED, the device's `SAFE_MODE` error is returned
unchanged; the tool never sends HELLO or re-arms. It is refused with `STATE_UNCONFIRMED`
when the state is unconfirmed, `CONTROL_BLOCKED` when blocked.

`all_open` sends `SW ALLOPEN` (legal in SAFE, ARMED and STAGING). It is not a recovery
tool. On success the device is empty and SAFE, but this layer only has the device's
success report, so the session becomes permanently unconfirmed: `get_state` returns
`STATE_UNCONFIRMED` with `netlist=null`, and create/modify/delete/plan/guide_leds return
`SESSION_REESTABLISH_REQUIRED` with no device I/O. `acknowledge_unresolved` (not exposed)
cannot lift this; build a new session (HELLO re-arms). A failed `all_open` leaves the state
unconfirmed, and also blocks writes unless it was a device-reported error.

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
    # Every result has device="MockDevice" (mock session), verified=False.
finally:
    session.close()
```

## MCP server

```sh
cd python && PYTHONPATH=src python3 -m jumperfree.agent.mcp_server [--port /dev/ttyACM0]
```

Without `--port` the session is a fresh `MockDevice`; with it, a serial session
(pyserial required, imported lazily). The server is hand-written newline-delimited
JSON-RPC 2.0 over stdio and supports `initialize`, `ping`, `tools/list` and
`tools/call`; notifications are ignored. Trade-off: the needed subset is small, so no
MCP SDK is used and the default install stays dependency-free (no `agent` extra).

- One owner, one session for the process lifetime, calls handled strictly in order;
  the session is closed on EOF.
- `tools/list` returns `TOOL_SCHEMAS`. Arguments must match the schema exactly
  (missing or extra keys → JSON-RPC `-32602`; nothing is guessed or dropped).
- `tools/call` returns the tool dict verbatim as `structuredContent` and as JSON
  text; `isError` is `not ok`. Error codes (`CONTROL_BLOCKED`, `SAFE_MODE`, ...) are
  never rewritten, retried or recovered by the server.

Example registration (MCP client config): command `python3`, args
`["-m", "jumperfree.agent.mcp_server"]`, env `PYTHONPATH=<repo>/python/src`.

## Limits

Software/Mock evidence only, single owner, sequential calls, initially empty device, no database
or persistence, no external-change monitoring or reconciliation. There is no
natural-language parser, LLM SDK, HTTP server, alternate adapter, hardware,
STM32 or firmware integration. Hardware wiring, timing and electrical behavior
remain unverified. The layer does not construct JF2 frames, CRCs, router paths,
Y channels, crosspoints or physical plans.
