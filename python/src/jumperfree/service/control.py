"""Mock-only intent orchestration over the existing router, planner and client."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..board import BoardProfile, compile_plan, state_with_tags
from ..core import JfError
from ..host import ApplyResult, DeviceClient, HostError
from ..routing.steiner import (
    crossbar_create_net, crossbar_delete_net, crossbar_init_state, crossbar_query,
)


@dataclass(frozen=True, slots=True)
class ControlResult:
    ok: bool
    operation: str
    netlist: dict[str, Any] | None = None
    router: dict[str, Any] | None = None
    error: JfError | None = None
    apply: ApplyResult | None = None
    device: str = field(default="MockDevice", init=False)
    verified: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok, "operation": self.operation,
            "netlist": copy.deepcopy(self.netlist), "router": copy.deepcopy(self.router),
            "error": copy.deepcopy(self.error.as_dict()) if self.error else None,
            "apply": self.apply.as_dict() if self.apply else None,
            "device": self.device, "verified": self.verified,
        }


class ControlService:
    """Own confirmed intent for one already-connected, initially empty MockDevice.

    The caller owns connect/close. This service never reconciles device state or
    clears DeviceClient's stop latch; query's router field is historical only.
    """

    def __init__(self, device_client: DeviceClient, board_profile: BoardProfile) -> None:
        initial = device_client.status
        if initial is None:
            raise HostError("NOT_INITIALIZED", "connect must succeed before creating ControlService")
        if any(initial.shadow.closed) or any(initial.shadow.tags):
            raise HostError("PLAN_STATE_MISMATCH", "ControlService requires an initially empty MockDevice")
        self._client = device_client
        self._profile = board_profile
        self._confirmed = crossbar_init_state(board_profile.topology)
        self._epoch = initial.epoch
        self._blocked = False
        self._unconfirmed = False

    def create(self, net_id: str, nodes: list[str]) -> ControlResult:
        return self._write("create", net_id, nodes)

    def modify(self, net_id: str, nodes: list[str]) -> ControlResult:
        return self._write("modify", net_id, nodes)

    def delete(self, net_id: str) -> ControlResult:
        return self._write("delete", net_id)

    def query(self) -> ControlResult:
        history = copy.deepcopy(crossbar_query(self._confirmed))
        if self._blocked or self._unconfirmed:
            return ControlResult(False, "query", router=history, error=HostError(
                "STATE_UNCONFIRMED", "only historical intent is available; device state is unconfirmed",
            ))
        return ControlResult(True, "query", netlist=copy.deepcopy(history), router=history)

    def acknowledge_unresolved(self) -> None:
        """Clear only the service write gate, without confirming or recovering anything."""
        self._blocked = False

    def _write(self, operation: str, net_id: str, nodes: list[str] | None = None) -> ControlResult:
        if self._blocked:
            return ControlResult(False, operation, error=HostError(
                "CONTROL_BLOCKED", "acknowledge_unresolved is required before further writes",
            ))
        router = None
        try:
            candidate = copy.deepcopy(self._confirmed)
            if operation in {"delete", "modify"}:
                candidate = crossbar_delete_net(candidate, net_id)
            if operation in {"create", "modify"}:
                candidate = crossbar_create_net(candidate, net_id, nodes)
            router = copy.deepcopy(crossbar_query(candidate))
            plan = compile_plan(self._confirmed, candidate, self._profile, base_epoch=self._epoch)
        except JfError as exc:
            return ControlResult(False, operation, router=router, error=exc)

        result = self._client.apply(plan)
        if not result.ok:
            if result.kind in {"unresolved", "timeout", "transport"}:
                self._blocked = True
            # Even a known rejection supplies no fresh confirmation of the device.
            self._unconfirmed = True
            return ControlResult(False, operation, router=router, error=result.error, apply=result)

        self._confirmed = state_with_tags(candidate, plan.tags)
        self._epoch = result.status.epoch
        self._unconfirmed = False
        return ControlResult(True, operation,
                             netlist=copy.deepcopy(crossbar_query(self._confirmed)),
                             router=router, apply=result)
