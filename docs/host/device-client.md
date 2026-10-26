# DeviceClient (JF2, MockDevice only)

From the repository's `python/` directory:

```sh
PYTHONPATH=src python3 -m jumperfree host device-demo --mock
PYTHONPATH=src python3 -m unittest discover -s tests -t . -v
# If pytest is installed, the same tests also run with:
PYTHONPATH=src python3 -m pytest
```

The demo connects to a fresh MockDevice, compiles and applies a plan containing
`keep` and `change` nets, then changes the latter while preserving the former.
It prints a human summary followed by one `HOST_JSON` line with both results,
epochs, shadow CRCs, and operation counts. It never opens a serial port.

Given an existing compiled `SwitchPlan` and its `BoardProfile`:

```python
from jumperfree.host import DeviceClient
from jumperfree.host.mock_device import MockDevice

client = DeviceClient(MockDevice(), board_id=profile.board_id,
                      topology_id=profile.topology_id,
                      max_ops=profile.limits.max_ops)
try:
    initial = client.connect()  # HELLO, capabilities, max_ops, status + tags
    result = client.apply(plan)  # plan.base_epoch must match initial.epoch
    print(result.as_dict())
    if result.ok:
        current = client.read_status()
finally:
    client.close()
```

`connect()` and `read_status()` raise `HostError`; `apply()` returns `ApplyResult`
with `kind=success|rejected|device_error|unresolved|timeout|transport`, preserving
the original `HostError.code`, details, and device error code. An apply before
successful initialization is `rejected / NOT_INITIALIZED`.

`success` requires both a matching COMMIT result (epoch, CRC, opened/closed)
and a consistent post-commit STATUS/TAGS snapshot matching the expected shadow.
`software-plan-ok` means compilation succeeded; `mock-device-applied` means the
memory model accepted and confirmed execution. Neither means STM32 or physical
connectivity was verified. All results have **`verified=false`**. Other Transport
implementations can report `device-reported-applied`; that is also unverified.
The host CLI provides only mock switch execution; the portable firmware core is
described in the [JF2 protocol documentation](../../protocol/jf2.md).

The caller supplies the profile's board/topology identity. HELLO must report
JF2 protocol 2, the expected board, two chips, and `sw,txn` capabilities. JF2 v0.1
does **not** transmit a topology hash, so the host can check the plan's hash
against the supplied profile, but cannot attest the device's physical topology.
The host does not route, modify netlists, or waive board hardware verification.

Before staging, the client requires ARMED with no reported faults, checks the
epoch, reads bitmaps and tags, verifies their CRC, and replays the existing plan
with the shared BBM helper. The replay must produce `plan.expect_crc`; nonzero
break tags must match the current tags. SwitchPlan has no separate base CRC:
these are the available preconditions, not proof of the original routing intent.
CRC32 is a consistency check, not authentication. No drift correction uses
ALLOPEN or FAULT CLEAR. ADD frames preserve `plan.to_wire_tokens()` order and use
the codec's 512-byte limit; the transaction respects device/profile max_ops.

Epoch/topology/plan-precondition rejection can be followed by a corrected plan.
An unsafe device state, inconsistent snapshot, communication error, or failure
after staging starts stops this client. Later apply/connect/read calls return
the saved failure without sending commands. Jf2Session alone handles retries
and COMMIT timeout → QUERY recovery. A recovered COMMITTED result has
`recovered=true`; ABORTED, UNKNOWN, OPEN, or an unsuccessful recovery never
triggers another whole-plan attempt. QUERY ABORTED reports `state_changed=unknown`:
lease expiry, a hardware fault, or an interrupted commit may have opened existing
connections and advanced the epoch. Failures once staging starts also report
`unknown`, including direct DEVICE_ERROR, timeout, and transport failures.
COMMIT_INTERRUPTED reports `yes` because it confirms an open-all and epoch
increment. A successful COMMIT reports `yes`, including when later confirmation
fails. These values describe the device shadow and epoch.

Use one client as the exclusive owner of its transport/session. Calls on that
client are serialized, including multi-frame transactions. External writers and
competing clients are unsupported. A staging failure may leave an open device
transaction; close only releases the transport. Reconcile the device's transaction,
epoch, shadow, and faults explicitly before creating a new client and compiling
a replacement plan. This API does not automatically abort, re-arm after faults,
clear connections, or retry an uncertain plan. STATUS/TAGS reads are checked for
consistency but cannot provide atomic snapshots against an external writer.

## LED batch and all-open

`led_batch(updates)` takes `{clip: (r,g,b)}` or `(clip, rgb)` pairs, validates everything
(clip 0..15, no duplicates, 0..255) before any I/O, and sends `LED BATCH n clip:rrggbb...`;
unlisted clips are unchanged. Device errors such as `SAFE_MODE` raise unchanged; it never
arms and does not set the stop latch. `all_open()` sends `SW ALLOPEN` in any state, then drops
`status` and requires a new `connect()` before `apply`; it does not clear the stop latch.
