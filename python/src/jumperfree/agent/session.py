"""One Agent conversation = one Host session over one board.

The session connects lazily on the first tool call, detects the board from JF2
INFO, arms it (HELLO), sets a 5 s LEASE and sends PING every second under the
same lock as tool calls. If the Host dies, the firmware opens every switch when
the lease expires. A normal close clears the LEDs and sends SW ALLOPEN.

Planning is read-only. A change runs only from a plan_id that this session
issued for the current state and that was shown on the LEDs first.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ..board import BoardProfile
from ..core import JfError
from ..core.geometry import geometry_for
from ..host import DeviceClient, HostError
from ..host.jf2_session import Jf2Session
from ..host.mock_device import MockDevice
from ..host.transport import Transport
from ..service.control import ControlResult, ControlService
from .ports import open_board, probe_info

LEASE_MS = 5000
HEARTBEAT_S = 1.0
MOCK_DEVICE_LABEL = "MockDevice"

Opener = Callable[[], "tuple[Transport, str, dict[str, str]]"]


@dataclass
class _Plan:
    action: str
    name: str
    nodes: list[str] | None
    version: int
    guided: bool = False
    summary: dict[str, Any] = field(default_factory=dict)


def _log(message: str) -> None:
    print(f"[jumperfree] {message}", file=sys.stderr, flush=True)


class AgentSession:
    """Single owner; every tool call and every heartbeat runs under one lock."""

    def __init__(self, opener: Opener, *, board: str | None = None, lease_ms: int = LEASE_MS,
                 heartbeat_s: float | None = HEARTBEAT_S) -> None:
        self._opener = opener
        self._expected_board = board
        self._lease_ms = lease_ms
        self._heartbeat_s = heartbeat_s
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._transport: Transport | None = None
        self._client: DeviceClient | None = None
        self._control: ControlService | None = None
        self._profile: BoardProfile | None = None
        self._label = "unconnected"
        self._lost: HostError | None = None
        self._closed = False
        self._version = 0
        self._plans: dict[str, _Plan] = {}
        self._ping_seq = 0
        self._last_up: int | None = None
        self.startup_notes: list[str] = []

    # ------------------------------------------------------------------ connect
    def _ensure(self) -> None:
        if self._closed:
            raise HostError("SESSION_CLOSED", "agent session is closed")
        if self._lost is not None:
            raise HostError("SESSION_LOST", "the device session ended; restart the Host to continue",
                            cause=self._lost.as_dict())
        if self._control is not None:
            return
        transport, label, info = self._opener()
        try:
            self._connect(transport, label, info)
        except BaseException:
            self._client = self._control = self._transport = None
            try:
                transport.close()
            except (OSError, HostError):
                pass
            raise

    def _connect(self, transport: Transport, label: str, info: dict[str, str]) -> None:
        board = info.get("board", "")
        profile = BoardProfile.for_board(board)
        if self._expected_board and self._expected_board not in (profile.board_id, profile.wire_board):
            raise HostError("BOARD_MISMATCH", "connected board differs from --board",
                            expected=self._expected_board, reported=board)
        notes = []
        fault = int(info.get("f", "0"), 16)
        if fault:
            # A previous Host that died leaves LEASE_EXPIRED latched. Clearing a latch
            # never closes a switch; an active hardware fault stays and is reported.
            reply = Jf2Session(transport, max_reply=1024).request(("FAULT", "CLEAR"))
            left = int(reply.kv.get("f", "0"), 16)
            notes.append(f"cleared latched faults 0x{fault:x}")
            if left:
                raise HostError("DEVICE_FAULT", "device has an active fault", faults=f"{left:x}")
        geometry = geometry_for(profile.wire_board)
        client = DeviceClient(transport, topology_id=profile.topology_id, board_id=profile.wire_board,
                              max_ops=profile.limits.max_ops, retries=0, geometry=geometry)
        self._transport, self._client = transport, client
        status = client.connect()
        if any(status.shadow.closed):
            # Connections from an earlier program: this Host has no intent for them.
            client.all_open()
            status = client.connect()
            notes.append("opened switches left by an earlier program")
        client.lease(self._lease_ms)
        client.led_batch({led: (0, 0, 0) for led in range(geometry.leds)})
        self._control = ControlService(client, profile, label)
        self._profile, self._label = profile, label
        self._last_up = None
        self.startup_notes = notes
        _log(f"connected {profile.name} on {label}; lease {self._lease_ms} ms" +
             (f"; {', '.join(notes)}" if notes else ""))
        if self._heartbeat_s and self._thread is None:
            self._thread = threading.Thread(target=self._heartbeat, name="jf-heartbeat", daemon=True)
            self._thread.start()

    # ---------------------------------------------------------------- heartbeat
    def _heartbeat(self) -> None:
        while not self._stop.wait(self._heartbeat_s):
            with self._lock:
                if self._closed or self._lost is not None or self._client is None:
                    continue
                self.beat()

    def beat(self) -> None:
        """One PING (caller holds the lock). Detects lease expiry, faults and reboots."""
        self._ping_seq += 1
        try:
            up, fault = self._client.ping(self._ping_seq)
        except HostError as exc:
            self._mark_lost(HostError("DEVICE_UNREACHABLE", "heartbeat failed", cause=exc.as_dict()))
            return
        if fault:
            self._mark_lost(HostError("DEVICE_FAULT", "device reported a fault; all switches are open",
                                      faults=f"{fault:x}", lease_expired=bool(fault & 1)))
        elif self._last_up is not None and up < self._last_up:
            self._mark_lost(HostError("DEVICE_REBOOTED", "device restarted (reset or emergency stop); "
                                      "all switches are open"))
        self._last_up = up

    def _mark_lost(self, error: HostError) -> None:
        self._lost = error
        self._plans.clear()
        if self._control is not None:
            self._control.mark_lost(error)
        _log(f"session lost: {error.code} {error.message}")

    # -------------------------------------------------------------------- tools
    def _locked(self, operation: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            try:
                self._ensure()
            except JfError as exc:
                return self._error(operation, exc)
            return fn()

    def _error(self, operation: str, error: JfError, **extra: Any) -> dict[str, Any]:
        result = ControlResult(False, operation, error=error, device=self._label).as_dict()
        result.update(extra)
        return result

    def describe_board(self) -> dict[str, Any]:
        return self._locked("describe_board", self._describe)

    def _describe(self) -> dict[str, Any]:
        profile = self._profile
        mini = profile.wire_board != "v01"
        clips: list[Any] = []
        for name, node in profile.nodes.items():
            if node["kind"] == "clip":
                clips.append({"node": name, "row": node["row"], "col": node["col"]} if mini else name)
        if mini:
            clips.sort(key=lambda clip: (clip["row"], clip["col"]))
        services = [{"node": name, "note": node.get("note", "")}
                    for name, node in profile.nodes.items() if node["kind"] == "service"]
        return {
            "ok": True, "operation": "describe_board", "device": self._label, "verified": False,
            "board": {"name": profile.name, "profile": profile.board_id, "firmware_board": profile.wire_board},
            "clips": clips,
            "rows": ({"A": "row far from the user (back row, USB side)",
                      "B": "row near the user (front row)",
                      "columns": "1..24 from the left, as the user sees the board"} if mini else None),
            "service_nodes": services,
            "capacity": {"max_nets": 15, "max_ops_per_change": profile.limits.max_ops,
                         "note": "one net uses one BUS; each clip in a net closes one switch"},
            "electrical": ("Each matrix path is about 100 Ohm (two switches). Signals and small currents "
                           "only: at most 15 mA per switch. Signals must stay within -7 V..+5 V. Power the "
                           "board first, then the user circuit; power down in reverse order."),
            "lease_ms": self._lease_ms, "startup_notes": list(self.startup_notes),
            "workflow": ["plan_net_change", "show the plan to the user and get a yes",
                         "guide_leds(plan_id) and tell the user each part's clips (row, column)",
                         "wait until the user says the parts are in place", "execute_plan(plan_id)"],
        }

    def get_state(self) -> dict[str, Any]:
        return self._locked("query", lambda: self._control.query().as_dict())

    def plan(self, action: str, name: str, nodes: list[str] | None = None) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            failure = _shape_error(self._label, "plan", action, name, nodes)
            if failure is not None:
                return failure
            result = self._control.plan(action, name, nodes).as_dict()
            if result["ok"]:
                result["plan_id"] = self._issue(action, name, nodes, result.get("plan", {}))
                result["next"] = ("Show this plan to the user and wait for a yes. Then call "
                                  "guide_leds(plan_id) and tell the user where each part goes.")
            return result
        return self._locked("plan", run)

    def guide(self, plan_id: str | None = None) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if plan_id is None:
                return self._control.guide_leds().as_dict()
            record = self._lookup(plan_id, "guide_leds")
            if isinstance(record, dict):
                return record
            result = self._control.guide_leds(record.action, record.name, record.nodes).as_dict()
            if result["ok"]:
                record.guided = True
                result["plan_id"] = plan_id
                result["next"] = ("Tell the user, part by part, which clips to use (row A = far side, "
                                  "row B = near side, column from the left). Wait until the user says "
                                  "everything is in place, then call execute_plan(plan_id).")
            return result
        return self._locked("guide_leds", run)

    def execute(self, plan_id: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            record = self._lookup(plan_id, "execute_plan")
            if isinstance(record, dict):
                return record
            if not record.guided:
                return self._error("execute_plan", HostError(
                    "PLAN_NOT_GUIDED", "show the plan on the LEDs (guide_leds) before executing it",
                    plan_id=plan_id))
            self._version += 1
            self._plans.clear()
            operation = {"create": self._control.create, "modify": self._control.modify,
                         "delete": self._control.delete}[record.action]
            args = (record.name,) if record.action == "delete" else (record.name, record.nodes)
            result = operation(*args).as_dict()
            result["plan_id"] = plan_id
            if result["ok"]:
                # Keep the LEDs on the confirmed netlist so the board shows what is live.
                self._control.guide_leds()
            return result
        return self._locked("execute_plan", run)

    def all_open(self) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            self._version += 1
            self._plans.clear()
            client = self._client
            try:
                client.led_batch({led: (0, 0, 0) for led in range(client.geometry.leds)})
            except HostError:
                pass  # LEDs are guidance only; opening the switches matters
            result = self._control.all_open().as_dict()
            if not result["ok"]:
                return result
            try:
                client.connect()  # new HELLO on a device that just reported all switches open
                client.lease(self._lease_ms)
                self._control = ControlService(client, self._profile, self._label)
            except JfError as exc:
                lost = HostError("SESSION_LOST", "re-arming after all_open failed", cause=exc.as_dict())
                self._mark_lost(lost)
                return self._error("all_open", lost)
            result["netlist"] = self._control.query().as_dict()["netlist"]
            result["note"] = "All switches are open and the LEDs are off. The session continues empty."
            return result
        return self._locked("all_open", run)

    # -------------------------------------------------------------------- plans
    def _issue(self, action: str, name: str, nodes: list[str] | None, summary: dict[str, Any]) -> str:
        key = json.dumps([action, name, nodes, self._version], sort_keys=True)
        plan_id = "p-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
        self._plans[plan_id] = _Plan(action, name, None if action == "delete" else list(nodes),
                                     self._version, summary=summary)
        return plan_id

    def _lookup(self, plan_id: object, operation: str) -> _Plan | dict[str, Any]:
        if not isinstance(plan_id, str) or plan_id not in self._plans:
            return self._error(operation, HostError(
                "PLAN_UNKNOWN", "no such plan for the current state; call plan_net_change again",
                plan_id=plan_id))
        record = self._plans[plan_id]
        if record.version != self._version:
            return self._error(operation, HostError("PLAN_STALE", "the state changed after this plan",
                                                    plan_id=plan_id))
        return record

    # -------------------------------------------------------------------- close
    def close(self) -> None:
        """Clear the LEDs and open every switch, then release the port. Idempotent."""
        self._stop.set()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            client = self._client
            if client is None:
                return
            if self._lost is None:
                try:
                    client.led_batch({led: (0, 0, 0) for led in range(client.geometry.leds)})
                except HostError as exc:
                    _log(f"exit: LED clear failed: {exc.code}")
                try:
                    client.all_open()
                    _log("exit: all switches open (SW ALLOPEN)")
                except HostError as exc:
                    _log(f"exit: SW ALLOPEN failed: {exc.code}; the {self._lease_ms} ms lease will open them")
            try:
                client.close()
            except HostError:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def __enter__(self) -> "AgentSession":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def device(self) -> Transport | None:
        return self._transport

    @property
    def profile(self) -> BoardProfile | None:
        return self._profile


def _shape_error(label: str, operation: str, action: object, name: object,
                 nodes: object) -> dict[str, Any] | None:
    error = None
    if not isinstance(action, str) or action not in {"create", "modify", "delete"}:
        error = HostError("BAD_ARG", "action must be create, modify or delete")
    elif not isinstance(name, str) or not name.strip():
        error = HostError("INVALID_NET_ID", "net name must be a non-empty string")
    elif action != "delete" and (not isinstance(nodes, list)
                                 or not all(isinstance(node, str) for node in nodes)):
        error = HostError("BAD_ARG", "nodes must be an array of node names")
    if error is None:
        return None
    return ControlResult(False, operation, error=error, device=label).as_dict()


def mock_opener(board: str = "mini", device: MockDevice | None = None) -> Opener:
    """Opener for an in-memory board; pass ``device`` to inspect it from tests."""
    def opener() -> tuple[Transport, str, dict[str, str]]:
        target = device or MockDevice(geometry=geometry_for(BoardProfile.for_board(board).wire_board))
        return target, MOCK_DEVICE_LABEL, probe_info(target)
    return opener


def open_session(mode: str = "auto", port: str | None = None, *, board: str | None = None,
                 lease_ms: int = LEASE_MS, heartbeat_s: float | None = HEARTBEAT_S,
                 serial_factory: Callable[..., Transport] | None = None) -> AgentSession:
    """``mock`` (in-memory board, default mini) or ``serial``/``auto`` (``port`` or search)."""
    if mode == "mock":
        return AgentSession(mock_opener(board or "mini"), board=board, lease_ms=lease_ms,
                            heartbeat_s=heartbeat_s)
    if mode in {"serial", "auto"}:
        kwargs = {} if serial_factory is None else {"serial_factory": serial_factory}

        def opener() -> tuple[Transport, str, dict[str, str]]:
            transport, name, info = open_board(port, **kwargs)
            return transport, f"serial:{name}", info
        return AgentSession(opener, board=board, lease_ms=lease_ms, heartbeat_s=heartbeat_s)
    raise HostError("BAD_ARG", "mode must be mock, serial or auto")
