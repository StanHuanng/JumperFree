"""Mock-only intent orchestration over the existing router, planner and client."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..board import BoardProfile, compile_led_plan, compile_plan, state_with_tags
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
    plan: dict[str, Any] | None = None
    led: dict[str, Any] | None = None
    device: str = field(default="MockDevice", init=False)
    verified: bool = field(default=False, init=False)

    def as_dict(self) -> dict[str, object]:
        data = {
            "ok": self.ok, "operation": self.operation,
            "netlist": copy.deepcopy(self.netlist), "router": copy.deepcopy(self.router),
            "error": copy.deepcopy(self.error.as_dict()) if self.error else None,
            "apply": self.apply.as_dict() if self.apply else None,
            "device": self.device, "verified": self.verified,
        }
        if self.plan is not None:  # only read-only planning results carry this key
            data["plan"] = copy.deepcopy(self.plan)
        if self.led is not None:  # only guide_leds results carry this key
            data["led"] = copy.deepcopy(self.led)
        return data


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
        self._all_opened = False  # permanent for this service; never cleared by acknowledge

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

    def guide_leds(self) -> ControlResult:
        """One LED BATCH of the confirmed netlist's colors; no switch change, no arming.

        Always sends all 16 clips: clips in a net get its palette color, every other clip
        is sent as 000000, so stale guidance is cleared and n == 16. SAFE_MODE and other
        device errors are returned unchanged; LED failures never block later writes.
        """
        if self._all_opened:
            return self._reestablish_result("guide_leds")
        if self._blocked:
            return self._blocked_result("guide_leds")
        if self._unconfirmed:
            return ControlResult(False, "guide_leds", error=HostError(
                "STATE_UNCONFIRMED", "device state is unconfirmed; refusing to guide from stale intent"))
        try:
            frames = compile_led_plan(self._confirmed).frames
            updates = {clip: tuple(frames.get(clip, (0, 0, 0))) for clip in range(16)}
            count = self._client.led_batch(updates)
        except (JfError, HostError) as exc:
            return ControlResult(False, "guide_leds", error=exc)
        led = {"count": count, "clips": {f"Clip{c}": "%02x%02x%02x" % rgb for c, rgb in updates.items()}}
        return ControlResult(True, "guide_leds", router=copy.deepcopy(crossbar_query(self._confirmed)),
                             led=led)

    def all_open(self) -> ControlResult:
        """Open every switch (legal in any state). Not a recovery path: the service stays
        unconfirmed with netlist=null and refuses all writes/plans/guides until a new
        session (HELLO + arm) is built. acknowledge_unresolved cannot lift this."""
        try:
            self._client.all_open()
        except HostError as exc:
            self._unconfirmed = True
            if exc.code != "DEVICE_ERROR":  # reply lost: the device may or may not be open
                self._blocked = True
            return ControlResult(False, "all_open", error=exc)
        self._all_opened = True
        self._unconfirmed = True
        return ControlResult(True, "all_open")

    def _reestablish_result(self, operation: str) -> ControlResult:
        return ControlResult(False, operation, error=HostError(
            "SESSION_REESTABLISH_REQUIRED", "all_open succeeded; build a new session (HELLO, arm) before further use",
        ))

    def plan(self, action: str, net_id: str, nodes: list[str] | None = None) -> ControlResult:
        """Route and compile a create/modify/delete on a copy; no device I/O, no state change."""
        if not isinstance(action, str) or action not in {"create", "modify", "delete"}:
            return ControlResult(False, "plan", error=HostError(
                "BAD_ARG", "action must be create, modify or delete"))
        if self._all_opened:
            return self._reestablish_result("plan")
        if self._blocked:
            return self._blocked_result("plan")
        router, plan, candidate, error = self._candidate(action, net_id, nodes)
        if error is not None:
            return ControlResult(False, "plan", router=router, error=error)
        occupied = sorted({node for net in router["nets"] for node in net["nodes"]})
        summary = {
            "action": action, "net": net_id, "base_epoch": plan.base_epoch,
            "breaks": len(plan.breaks), "makes": len(plan.makes),
            "disturbed_nets": list(plan.disturbed_nets), "occupied_nodes": occupied,
            "occupancy": copy.deepcopy(router["occupancy"]),
        }
        return ControlResult(True, "plan", router=router, plan=summary)

    def _blocked_result(self, operation: str) -> ControlResult:
        return ControlResult(False, operation, error=HostError(
            "CONTROL_BLOCKED", "acknowledge_unresolved is required before further writes",
        ))

    def _candidate(self, operation: str, net_id: str, nodes: list[str] | None):
        """Return (router, plan, candidate, error); the confirmed state is never mutated."""
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
            return router, None, None, exc
        return router, plan, candidate, None

    def _write(self, operation: str, net_id: str, nodes: list[str] | None = None) -> ControlResult:
        if self._all_opened:
            return self._reestablish_result(operation)
        if self._blocked:
            return self._blocked_result(operation)
        router, plan, candidate, error = self._candidate(operation, net_id, nodes)
        if error is not None:
            return ControlResult(False, operation, router=router, error=error)

        result = self._client.apply(plan)
        if not result.ok:
            if (result.kind in {"unresolved", "timeout", "transport"}
                    or result.state_changed in {"yes", "unknown"}):
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
