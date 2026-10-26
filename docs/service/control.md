# ControlService v0.1

Run the mock demonstration from `python/`:

```sh
PYTHONPATH=src python3 -m jumperfree host control-demo --mock
```

It prints a summary and canonical `HOST_JSON`. A–D create NET_A, add NET_B,
modify A, then delete A while preserving B. E checks invalid nodes, ownership
conflicts, a real Router failure on a separate disconnected software profile,
and an interrupted MockDevice commit. Expected rejections pass; unexpected
results or connection/close failures produce a nonzero exit code.

For Python callers, connect an empty MockDevice before constructing the service
and close the client in `finally`:

```python
from jumperfree.board import BoardProfile
from jumperfree.host import DeviceClient
from jumperfree.host.mock_device import MockDevice
from jumperfree.service.control import ControlService

profile = BoardProfile.load()
client = DeviceClient(MockDevice(), topology_id=profile.topology_id,
                      board_id=profile.board_id, max_ops=profile.limits.max_ops)
try:
    client.connect()
    control = ControlService(client, profile)
    result = control.create("NET_A", ["Clip1", "Clip8"])
    print(result.as_dict())
    control.modify("NET_A", ["Clip1", "Clip10"])
    control.query()
    control.delete("NET_A")
finally:
    client.close()
```

`create(net_id, nodes)`, `modify(net_id, nodes)`, `delete(net_id)` and `query()`
return `ControlResult`. IDs are strings and nodes are lists of strings. Results
contain `ok`, `operation`, `netlist`, `router`, `error`, `apply`,
`device="MockDevice"` and `verified=False`; `as_dict()` serializes existing errors
and ApplyResult without replacing their codes, messages or details.

Confirmed intent starts empty with the connected epoch. Each write routes a
deep copy, compiles against confirmed intent and that epoch, and applies once.
Modify deletes and recreates the net on this copy before a single apply.
Only an `ApplyResult.ok` advances confirmed intent, retained net tags and epoch.
Routing/compilation errors never apply. No failed apply is retried automatically.

Successful `netlist` is a detached `crossbar_query` view. Failures return
`netlist=null`. Write `router` describes the candidate if routing completed;
query `router` describes historical confirmed intent. Query does no device I/O.
After any failed apply, query conservatively returns `STATE_UNCONFIRMED` and
`netlist=null`; its historical router view does not assert current device state.

`unresolved`, `timeout` and `transport` apply results, and any failed apply with
`state_changed="yes"` or `"unknown"`, block all writes before routing or execution
(`CONTROL_BLOCKED`). This includes interrupted commits and in-flight transaction
failures that may have opened all connections after a lease expiry or fault.
`acknowledge_unresolved()` returns
`None` and clears only that service flag. It does not accept a candidate, change
confirmed intent/epoch, refresh query, clear DeviceClient's stop, clear device
faults or prove the device unchanged. A subsequent write still encounters the
client's stop. Reconciliation and automatic recovery are outside v0.1.

This is an in-memory, single-owner, empty-Mock-only composition. It does not
restore intent from an existing device shadow, monitor external changes, persist
state or support concurrent owners. Board wiring, timing and electrical safety
remain unverified on hardware; Mock success is software evidence only. There is
no Agent/NL, HTTP, database or STM32 integration. A future Agent adapter should
call these methods and preserve their structured errors and unresolved-state
gate; it must not turn acknowledgment into a retry or recovery policy.
