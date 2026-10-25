"""A–E control scenarios using real routing and MockDevice, never hardware."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from typing import Iterator

from ..board import BoardProfile
from ..core import JfError, canonical_json
from ..host import DeviceClient, HostError
from ..host.mock_device import MockDevice
from .control import ControlResult, ControlService


@contextmanager
def _connected(profile: BoardProfile, device: MockDevice) -> Iterator[ControlService]:
    client = DeviceClient(device, topology_id=profile.topology_id,
                          board_id=profile.board_id, max_ops=profile.limits.max_ops)
    primary_error: JfError | None = None
    try:
        client.connect()
        yield ControlService(client, profile)
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


def _record(results: list[dict[str, object]], scenario: str, result: ControlResult, *,
            expected_error: str | None = None,
            expected_nets: dict[str, list[str]] | None = None) -> None:
    passed = (result.ok if expected_error is None else
              not result.ok and result.error is not None and result.error.code == expected_error)
    if expected_nets is not None:
        actual = ({net["net"]: net["nodes"] for net in result.netlist["nets"]}
                  if result.netlist is not None else None)
        passed = passed and actual == expected_nets
    results.append({"scenario": scenario, "expected_error": expected_error,
                    "passed": passed, **result.as_dict()})
    if not passed:
        raise JfError("CONTROL_DEMO_FAILED", "unexpected control scenario result", scenario=scenario)


def run_control_demo(args: argparse.Namespace) -> int:
    results: list[dict[str, object]] = []
    payload: dict[str, object] = {
        "operation": "control-demo", "transport": "mock", "device": "MockDevice",
        "verified": False, "results": results,
    }
    try:
        profile = BoardProfile.load()
        device = MockDevice()
        with _connected(profile, device) as service:
            _record(results, "A", service.create("NET_A", ["Clip1", "Clip8"]),
                    expected_nets={"NET_A": ["Clip1", "Clip8"]})
            _record(results, "B", service.create("NET_B", ["Clip2", "Clip9"]),
                    expected_nets={"NET_A": ["Clip1", "Clip8"], "NET_B": ["Clip2", "Clip9"]})
            _record(results, "C", service.modify("NET_A", ["Clip1", "Clip10"]),
                    expected_nets={"NET_A": ["Clip1", "Clip10"], "NET_B": ["Clip2", "Clip9"]})
            _record(results, "D", service.delete("NET_A"),
                    expected_nets={"NET_B": ["Clip2", "Clip9"]})
            _record(results, "E-invalid-node", service.create("invalid", ["Clip1", "missing"]),
                    expected_error="NODE_NOT_IN_TOPOLOGY")
            _record(results, "E-net-conflict", service.create("conflict", ["Clip2", "Clip10"]),
                    expected_error="NODES_ALREADY_IN_OTHER_NET")

            # A disconnected software fixture makes NO_FREE_PATH reproducible;
            # the normal eight-channel topology has enough lanes for all Clips.
            topology = profile.topology
            topology["inter_chip_links"] = []
            isolated_profile = replace(profile, topology_json=canonical_json(topology))
            with _connected(isolated_profile, MockDevice()) as isolated:
                _record(results, "E-route-failure", isolated.create("unreachable", ["Clip1", "Clip8"]),
                        expected_error="NO_FREE_PATH")

            device.interrupt_next_commit()
            _record(results, "E-device-failure", service.create("interrupted", ["Clip1", "Clip8"]),
                    expected_error="DEVICE_ERROR")
        payload["ok"] = True
        summary = "HOST control-demo OK: scenarios A–E on MockDevice; hardware verified=false"
    except JfError as exc:
        payload.update(ok=False, error=exc.as_dict())
        summary = f"HOST control-demo FAIL {exc.code}: {exc.message}"
    print(summary)
    print(f"HOST_JSON {canonical_json(payload)}")
    return 0 if payload["ok"] else 1
