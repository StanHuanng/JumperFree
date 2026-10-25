"""Mock-only composition of routing fixtures, board plans, and the host executor."""

from __future__ import annotations

import argparse

from ..board import BoardProfile, compile_plan, state_with_tags
from ..core import JfError, canonical_json
from ..host import DeviceClient, HostError
from ..host.mock_device import MockDevice
from ..routing.steiner import crossbar_create_net, crossbar_delete_net, crossbar_init_state


def run_device_demo(args: argparse.Namespace) -> int:
    results = []
    payload: dict[str, object] = {
        "operation": "device-demo", "transport": "mock", "verified": False, "results": results,
    }
    try:
        profile = BoardProfile.load()
        empty = crossbar_init_state(profile.topology)
        first = crossbar_create_net(empty, "keep", ["Clip1", "Clip2"])
        first = crossbar_create_net(first, "change", ["Clip0", "Clip15"])
        plan = compile_plan(empty, first, profile)
        client = DeviceClient(MockDevice(), topology_id=profile.topology_id,
                              board_id=profile.board_id, max_ops=profile.limits.max_ops)
        primary_error: JfError | None = None
        try:
            initial = client.connect()
            payload["initial"] = initial.as_dict()
            for step in (1, 2):
                result = client.apply(plan)
                results.append({"step": step, "plan_kind": "software-plan-ok", **result.as_dict()})
                if not result.ok:
                    assert result.error is not None
                    raise result.error
                if step == 1:
                    first = state_with_tags(first, plan.tags)
                    second = crossbar_delete_net(first, "change")
                    second = crossbar_create_net(second, "change", ["Clip0", "Clip14"])
                    plan = compile_plan(first, second, profile, base_epoch=result.status.epoch)
            payload["ok"] = True
        except JfError as exc:
            primary_error = exc
            raise
        finally:
            try:
                client.close()
            except HostError as exc:
                if primary_error is None:
                    raise
                primary_error.details["close_error"] = exc.as_dict()
        summary = "HOST device-demo OK: two plans applied to MockDevice; hardware verified=false"
    except JfError as exc:
        payload.update(ok=False, error=exc.as_dict())
        summary = f"HOST device-demo FAIL {exc.code}: {exc.message}"
    print(summary)
    print(f"HOST_JSON {canonical_json(payload)}")
    return 0 if payload["ok"] else 1
