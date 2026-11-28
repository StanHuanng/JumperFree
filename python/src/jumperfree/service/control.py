"""Mock-only intent orchestration over the existing router, planner and client."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any

from ..board import TAG_PALETTE, BoardProfile, compile_led_plan, compile_plan, net_tags, state_with_tags
from ..core import JfError
from ..host import ApplyResult, DeviceClient, HostError
from ..routing.steiner import (
    crossbar_create_net, crossbar_delete_net, crossbar_init_state, crossbar_query,
)


# CH446Q limits (project hardware constraints): at most 15 mA continuous per switch,
# at most 100 mA through the whole matrix. A net that declares current_ma gets
# ceil(current_ma / 15) paths in parallel; the declared currents of all nets
# together may not exceed 100 mA (a supply net and its return both count).
SWITCH_MA = 15
MATRIX_MA = 100
PATH_OHM = 100  # clip-to-clip path, two switches in series (bench 2026-11-23: about 108 Ohm)


def paths_for(current_ma: int | None) -> int:
    """Number of parallel paths for a declared current (None = signal net, 1 path)."""
    if current_ma is None:
        return 1
    if type(current_ma) is not int or current_ma < 1:
        raise HostError("BAD_ARG", "current_ma must be a positive integer (mA)")
    if current_ma > MATRIX_MA:
        raise HostError("CURRENT_TOO_HIGH", f"the matrix carries at most {MATRIX_MA} mA in total",
                        current_ma=current_ma, limit_ma=MATRIX_MA)
    return max(1, math.ceil(current_ma / SWITCH_MA))


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
    device: str = "MockDevice"
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

    def __init__(self, device_client: DeviceClient, board_profile: BoardProfile,
                 device_label: str = "MockDevice") -> None:
        initial = device_client.status
        if initial is None:
            raise HostError("NOT_INITIALIZED", "connect must succeed before creating ControlService")
        if any(initial.shadow.closed) or any(initial.shadow.tags):
            raise HostError("PLAN_STATE_MISMATCH", "ControlService requires an initially empty MockDevice")
        self._device_label = device_label
        self._client = device_client
        self._profile = board_profile
        self._confirmed = crossbar_init_state(board_profile.topology)
        self._epoch = initial.epoch
        self._blocked = False
        self._unconfirmed = False
        self._all_opened = False  # permanent for this service; never cleared by acknowledge
        self._lost: JfError | None = None

    def _result(self, *args: Any, **kwargs: Any) -> ControlResult:
        return ControlResult(*args, **kwargs, device=self._device_label)

    def create(self, net_id: str, nodes: list[str], current_ma: int | None = None) -> ControlResult:
        return self._write("create", net_id, nodes, current_ma)

    def modify(self, net_id: str, nodes: list[str], current_ma: int | None = None) -> ControlResult:
        return self._write("modify", net_id, nodes, current_ma)

    def delete(self, net_id: str) -> ControlResult:
        return self._write("delete", net_id)

    def query(self) -> ControlResult:
        history = copy.deepcopy(crossbar_query(self._confirmed))
        if self._blocked or self._unconfirmed:
            return self._result(False, "query", router=history, error=HostError(
                "STATE_UNCONFIRMED", "only historical intent is available; device state is unconfirmed",
            ))
        return self._result(True, "query", netlist=copy.deepcopy(history), router=history)

    def acknowledge_unresolved(self) -> None:
        """Clear only the service write gate, without confirming or recovering anything."""
        self._blocked = False

    def guide_leds(self, action: str | None = None, net_id: str | None = None,
                   nodes: list[str] | None = None, current_ma: int | None = None) -> ControlResult:
        """One LED BATCH of the confirmed netlist's colors; no switch change, no arming.

        With action/net_id/nodes (same meaning as plan) the colors are those of the
        candidate netlist that plan would produce, so nodes can be lit before executing;
        the confirmed state is not changed. Without them the confirmed netlist is lit.

        Always sends all 16 clips: clips in a net get its palette color, every other clip
        is sent as 000000, so stale guidance is cleared and n == 16. SAFE_MODE and other
        device errors are returned unchanged; LED failures never block later writes.
        """
        if self._all_opened:
            return self._reestablish_result("guide_leds")
        if self._blocked:
            return self._blocked_result("guide_leds")
        if self._unconfirmed:
            return self._result(False, "guide_leds", error=HostError(
                "STATE_UNCONFIRMED", "device state is unconfirmed; refusing to guide from stale intent"))
        source, state = "confirmed", self._confirmed
        if action is not None:
            if not isinstance(action, str) or action not in {"create", "modify", "delete"}:
                return self._result(False, "guide_leds", error=HostError(
                    "BAD_ARG", "action must be create, modify or delete"))
            router, _, candidate, error = self._candidate(action, net_id, nodes, current_ma)
            if error is not None:
                return self._result(False, "guide_leds", router=router, error=error)
            source, state = "plan", candidate
        try:
            if self._profile.wire_board == "v01":
                frames = compile_led_plan(state).frames
                updates = {clip: tuple(frames.get(clip, (0, 0, 0))) for clip in range(16)}
            else:
                frames, updates = self._mini_frames(state)
            count = self._client.led_batch(updates)
        except (JfError, HostError) as exc:
            return self._result(False, "guide_leds", error=exc)
        if self._profile.wire_board == "v01":
            led = {"source": source, "count": count,
                   "clips": {f"Clip{c}": "%02x%02x%02x" % rgb for c, rgb in updates.items()}}
        else:
            led = self._mini_guide(source, count, state, frames)
        return self._result(True, "guide_leds", router=copy.deepcopy(crossbar_query(state)),
                             led=led)

    def _mini_frames(self, state: dict[str, Any]):
        tags = net_tags(state)
        frames = compile_led_plan(state, TAG_PALETTE, self._profile.led_map, tags).frames
        updates = {led: tuple(frames.get(led, (0, 0, 0))) for led in range(self._profile.geometry.leds)}
        return frames, updates

    def _mini_guide(self, source: str, count: int, state: dict[str, Any], frames) -> dict[str, Any]:
        """Placement guide: every lit clip with its net, color and row/column."""
        nodes = self._profile.nodes
        before = {node: name for name, net in self._confirmed["nets"].items() for node in net["nodes"]}
        after = {node: name for name, net in state["nets"].items() for node in net["nodes"]}
        nets = []
        for name, tag in net_tags(state).items():
            members = state["nets"][name]["nodes"]
            nets.append({"net": name, "color": "%02x%02x%02x" % TAG_PALETTE[tag - 1],
                         "nodes": [_describe(node, nodes[node]) for node in members]})
        return {
            "source": source, "count": count, "lit": len(frames), "nets": nets,
            "added_nodes": sorted(node for node in after if before.get(node) != after[node]),
            "removed_nodes": sorted(node for node in before if before[node] != after.get(node)),
        }

    def mark_lost(self, error: JfError) -> None:
        """The device session ended outside this service (lease expiry, fault, reboot,
        lost transport). Intent is history only and no further write is allowed."""
        self._blocked = True
        self._unconfirmed = True
        self._lost = error

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
            return self._result(False, "all_open", error=exc)
        self._all_opened = True
        self._unconfirmed = True
        return self._result(True, "all_open")

    def _reestablish_result(self, operation: str) -> ControlResult:
        return self._result(False, operation, error=HostError(
            "SESSION_REESTABLISH_REQUIRED", "all_open succeeded; build a new session (HELLO, arm) before further use",
        ))

    def plan(self, action: str, net_id: str, nodes: list[str] | None = None,
             current_ma: int | None = None) -> ControlResult:
        """Route and compile a create/modify/delete on a copy; no device I/O, no state change."""
        if not isinstance(action, str) or action not in {"create", "modify", "delete"}:
            return self._result(False, "plan", error=HostError(
                "BAD_ARG", "action must be create, modify or delete"))
        if self._all_opened:
            return self._reestablish_result("plan")
        if self._blocked:
            return self._blocked_result("plan")
        router, plan, candidate, error = self._candidate(action, net_id, nodes, current_ma)
        if error is not None:
            return self._result(False, "plan", router=router, error=error)
        occupied = sorted({node for net in router["nets"] for node in net["nodes"]})
        summary = {
            "action": action, "net": net_id, "base_epoch": plan.base_epoch,
            "breaks": len(plan.breaks), "makes": len(plan.makes),
            "disturbed_nets": list(plan.disturbed_nets), "occupied_nodes": occupied,
            "occupancy": copy.deepcopy(router["occupancy"]),
        }
        if action != "delete":
            paths = paths_for(current_ma)
            summary.update({
                "current_ma": current_ma, "parallel_paths": paths,
                "path_ohm_typical": round(PATH_OHM / paths),
                "max_ma": paths * SWITCH_MA,
            })
        return self._result(True, "plan", router=router, plan=summary)

    def _blocked_result(self, operation: str) -> ControlResult:
        if self._lost is not None:
            return self._result(False, operation, error=HostError(
                "SESSION_LOST", "the device session ended; restart the Host to continue",
                cause=self._lost.as_dict()))
        return self._result(False, operation, error=HostError(
            "CONTROL_BLOCKED", "acknowledge_unresolved is required before further writes",
        ))

    def _candidate(self, operation: str, net_id: str, nodes: list[str] | None,
                   current_ma: int | None = None):
        """Return (router, plan, candidate, error); the confirmed state is never mutated."""
        router = None
        try:
            paths = paths_for(current_ma)
            if operation == "delete" and current_ma is not None:
                raise HostError("BAD_ARG", "current_ma is only for create and modify")
        except HostError as exc:
            return None, None, None, exc
        if operation in {"create", "modify"} and isinstance(nodes, list):
            known = self._profile.nodes
            hidden = sorted({n for n in nodes if isinstance(n, str) and n in known
                             and known[n]["kind"] not in {"clip", "service"}})
            if hidden:
                return None, None, None, HostError(
                    "NODE_RESERVED", "reserved nodes cannot be used in a net", nodes=hidden)
            if {"TEST_SRC", "SVC_GND"} <= set(nodes) and self._profile.wire_board != "v01":
                return None, None, None, HostError(
                    "SERVICE_SHORT", "TEST_SRC and SVC_GND cannot share a net")
        try:
            candidate = copy.deepcopy(self._confirmed)
            if operation in {"delete", "modify"}:
                candidate = crossbar_delete_net(candidate, net_id)
            if operation in {"create", "modify"}:
                candidate = crossbar_create_net(candidate, net_id, nodes, parallel=paths)
                if current_ma is not None:
                    candidate["nets"][net_id]["current_ma"] = current_ma
                total = sum(net.get("current_ma", 0) for net in candidate["nets"].values())
                if total > MATRIX_MA:
                    raise HostError("CURRENT_TOO_HIGH",
                                    f"declared currents of all nets exceed {MATRIX_MA} mA",
                                    total_ma=total, limit_ma=MATRIX_MA)
            router = copy.deepcopy(crossbar_query(candidate))
            plan = compile_plan(self._confirmed, candidate, self._profile, base_epoch=self._epoch)
        except JfError as exc:
            return router, None, None, exc
        return router, plan, candidate, None

    def _write(self, operation: str, net_id: str, nodes: list[str] | None = None,
               current_ma: int | None = None) -> ControlResult:
        if self._all_opened:
            return self._reestablish_result(operation)
        if self._blocked:
            return self._blocked_result(operation)
        router, plan, candidate, error = self._candidate(operation, net_id, nodes, current_ma)
        if error is not None:
            return self._result(False, operation, router=router, error=error)

        result = self._client.apply(plan)
        if not result.ok:
            if (result.kind in {"unresolved", "timeout", "transport"}
                    or result.state_changed in {"yes", "unknown"}):
                self._blocked = True
            # Even a known rejection supplies no fresh confirmation of the device.
            self._unconfirmed = True
            return self._result(False, operation, router=router, error=result.error, apply=result)

        self._confirmed = state_with_tags(candidate, plan.tags)
        self._epoch = result.status.epoch
        self._unconfirmed = False
        return self._result(True, operation,
                             netlist=copy.deepcopy(crossbar_query(self._confirmed)),
                             router=router, apply=result)


def _describe(name: str, node: dict[str, Any]) -> dict[str, Any]:
    if node.get("kind") == "clip":
        row = node["row"]
        side = "far side (A, back)" if row == "A" else "near side (B, front)"
        return {"node": name, "row": row, "col": node["col"], "where": f"row {row} column {node['col']}, {side}"}
    return {"node": name, "where": "internal service channel, nothing to plug"}
