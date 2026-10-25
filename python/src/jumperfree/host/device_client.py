"""JF2 session and SwitchPlan execution; no routing or hardware verification."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
import secrets
import threading
import time
from typing import Callable, Literal, Protocol

from ..core import JfError, SCHEMA
from ..core.shadow import OpLike, Shadow, apply_ops, parse_bitmap_hex, shadow_crc
from .errors import HostError
from .jf2 import Jf2Response, commit_crc, encode_jf2, uint
from .jf2_session import Jf2Session
from .mock_device import MockDevice
from .transport import Transport


class SwitchPlanLike(Protocol):
    """Structural boundary: board.SwitchPlan implements this without a host import."""

    schema: str
    base_epoch: int
    topology_id: str
    breaks: tuple[OpLike, ...]
    makes: tuple[OpLike, ...]
    expect_crc: int

    def to_wire_tokens(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    epoch: int
    crc: int
    state: str
    shadow: Shadow

    def as_dict(self) -> dict[str, object]:
        return {"epoch": self.epoch, "crc": f"{self.crc:08x}", "state": self.state,
                "c0": self.shadow.bitmap_hex(0), "c1": self.shadow.bitmap_hex(1)}


@dataclass(frozen=True, slots=True)
class ApplyResult:
    kind: Literal["success", "rejected", "device_error", "unresolved", "timeout", "transport"]
    transaction_id: int | None = None
    status: DeviceStatus | None = None
    opened: int | None = None
    closed: int | None = None
    recovered: bool = False
    evidence: str | None = None
    state_changed: str = "no"
    error: HostError | None = None
    verified: bool = field(default=False, init=False)

    @property
    def ok(self) -> bool:
        return self.kind == "success"

    def as_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "ok": self.ok, "transaction_id": self.transaction_id,
                "status": self.status.as_dict() if self.status else None,
                "opened": self.opened, "closed": self.closed, "recovered": self.recovered,
                "evidence": self.evidence, "state_changed": self.state_changed,
                "verified": self.verified, "error": self.error.as_dict() if self.error else None}


def _number(response: Jf2Response, key: str) -> int:
    try:
        return uint(response.kv[key])
    except (KeyError, HostError) as exc:
        raise HostError("MALFORMED_RESPONSE", "missing or invalid " + key) from exc


def _hex(response: Jf2Response, key: str, digits: int) -> str:
    value = response.kv.get(key, "")
    if not re.fullmatch(r"[0-9a-f]{" + str(digits) + r"}", value):
        raise HostError("MALFORMED_RESPONSE", "missing or invalid " + key)
    return value


def _require_no_fault(response: Jf2Response) -> None:
    fault = response.kv.get("f", "0")
    if not re.fullmatch(r"[0-9a-f]+", fault):
        raise HostError("MALFORMED_RESPONSE", "invalid fault mask")
    if int(fault, 16):
        raise HostError("DEVICE_FAULT", "device has uncleared faults", faults=fault,
                        state=response.kv.get("state"))


def _require_armed(response: Jf2Response) -> None:
    _require_no_fault(response)
    if response.kv.get("state") != "ARMED":
        raise HostError("STATE_MISMATCH", "device must be ARMED",
                        state=response.kv.get("state"))


class DeviceClient:
    """Single-owner executor over a Transport.

    The caller supplies board/profile identity; JF2 v0.1 has no topology hash.
    connect/read_status raise HostError, while apply returns an ApplyResult.
    Transaction/communication failures latch a stop until explicit reconciliation
    outside this client. Neither reconnect nor apply clears faults or user nets.
    """

    def __init__(self, transport: Transport, *, topology_id: str, board_id: str = "v01",
                 max_ops: int = 256, clock: Callable[[], float] = time.monotonic,
                 frame_timeout: float = 0.3, retries: int = 2) -> None:
        if (not isinstance(topology_id, str) or not topology_id
                or not isinstance(board_id, str) or not board_id
                or type(max_ops) is not int or not 0 <= max_ops <= 256):
            raise HostError("BAD_ARG", "expected board/topology identity and max_ops in 0..256")
        self._session = Jf2Session(transport, clock=clock, frame_timeout=frame_timeout, retries=retries)
        self._lock = threading.Lock()
        self._board_id = board_id
        self._topology_id = topology_id
        self._max_ops = max_ops
        # Decimal uint32 on the wire, monotonic within this client (including reconnects).
        self._next_id = secrets.randbelow(1 << 31)
        self._mock = isinstance(transport, MockDevice)
        self._initialized = False
        self._closed = False
        self._stopped: HostError | None = None
        self._stopped_id: int | None = None
        self._stopped_changed = "no"
        self.status: DeviceStatus | None = None

    def _ready(self) -> None:
        if self._closed:
            raise HostError("SESSION_CLOSED", "client is closed")
        if self._stopped is not None:
            raise self._stopped
        if not self._initialized:
            raise HostError("NOT_INITIALIZED", "connect must succeed before executing a plan")

    def connect(self) -> DeviceStatus:
        with self._lock:
            if self._closed:
                raise HostError("SESSION_CLOSED", "client is closed")
            if self._stopped is not None:
                raise self._stopped
            self._initialized = False
            self.status = None
            hello = self._session.request(("HELLO", "2", secrets.token_hex(4)))
            if hello.kv.get("proto") != "2":
                raise HostError("PROTOCOL_MISMATCH", "device must support JF2 protocol 2")
            if hello.kv.get("board") != self._board_id or hello.kv.get("chips") != "2":
                raise HostError("BOARD_MISMATCH", "HELLO board/chip identity differs",
                                expected=self._board_id, reported=hello.kv.get("board"))
            if not {"sw", "txn"}.issubset(hello.kv.get("caps", "").split(",")):
                raise HostError("CAPABILITY_MISMATCH", "device needs sw and txn capabilities")
            _hex(hello, "boot", 8)
            _require_armed(hello)
            config = self._session.request(("CFG", "GET", "max_ops"))
            self._max_ops = min(self._max_ops, _number(config, "value"))
            status = self._read_status()
            if status.epoch != _number(hello, "epoch"):
                raise HostError("EPOCH_MISMATCH", "device changed during initialization")
            self.status = status
            self._initialized = True
            return status

    def _read_status(self) -> DeviceStatus:
        before = self._session.request(("SW", "STATUS"))
        _require_armed(before)
        epoch, crc = _number(before, "epoch"), int(_hex(before, "crc", 8), 16)
        closed = tuple(parse_bitmap_hex(_hex(before, f"c{chip}", 32)) for chip in range(2))
        tags_reply = self._session.request(("SW", "TAGS"))
        # TAGS carries faults but no state field.
        _require_no_fault(tags_reply)
        tags = bytearray()
        for chip in range(2):
            for packed in bytes.fromhex(_hex(tags_reply, f"t{chip}", 64)):
                tags.extend((packed & 15, packed >> 4))
            tags.extend(bytes(64))  # X8..15 reserved, omitted by SW TAGS.
        after = self._session.request(("SW", "STATUS"))
        _require_armed(after)
        if any(before.kv.get(key) != after.kv.get(key) for key in ("epoch", "crc", "c0", "c1", "state")):
            raise HostError("STATE_MISMATCH", "shadow changed while reading status/tags")
        shadow = Shadow(closed, bytes(tags))
        if (any(bitmap >> 64 for bitmap in closed)
                or any(bool(closed[chip] & (1 << bit)) != bool(tags[chip * 128 + bit])
                       for chip in range(2) for bit in range(128))
                or shadow_crc(shadow) != crc):
            raise HostError("SHADOW_MISMATCH", "status bitmaps/tags do not match shadow CRC")
        return DeviceStatus(epoch, crc, "ARMED", shadow)

    def read_status(self) -> DeviceStatus:
        with self._lock:
            self._ready()
            try:
                self.status = self._read_status()
                return self.status
            except HostError as exc:
                self._stopped = exc
                raise

    @staticmethod
    def _add_frames(id_: str, tokens: list[str]) -> list[tuple[str, ...]]:
        frames: list[tuple[str, ...]] = []
        chunk: list[str] = []
        for token in tokens:
            prefix = ("TXN", "ADD", id_, str(len(frames)))
            try:
                encode_jf2((*prefix, *chunk, token))
            except HostError as exc:
                if exc.code != "BAD_FRAME" or not chunk:
                    raise
                frames.append((*prefix, *chunk))
                chunk = []
                encode_jf2(("TXN", "ADD", id_, str(len(frames)), token))
            chunk.append(token)
        if chunk:
            frames.append(("TXN", "ADD", id_, str(len(frames)), *chunk))
        return frames

    @staticmethod
    def _failure(error: HostError, transaction_id: int | None, changed: str) -> ApplyResult:
        kind = "rejected"
        if error.code == "COMMIT_UNRESOLVED" or error.details.get("state_changed") == "unknown":
            kind = "unresolved"
        elif error.code == "DEVICE_ERROR":
            kind = "device_error"
        elif error.code == "TIMEOUT":
            kind = "timeout"
        elif error.code in {"TRANSPORT_ERROR", "SHORT_WRITE", "SESSION_CLOSED"}:
            kind = "transport"
        elif changed == "yes":
            kind = "device_error"
        return ApplyResult(kind, transaction_id, error=error,
                           state_changed=str(error.details.get("state_changed", changed)))

    def apply(self, plan: SwitchPlanLike) -> ApplyResult:
        with self._lock:
            transaction_id = None
            phase = "precheck"
            changed = "no"
            try:
                self._ready()
                if plan.schema != SCHEMA["plan"] or plan.topology_id != self._topology_id:
                    raise HostError("PLAN_TOPOLOGY_MISMATCH", "plan schema/topology differs from profile")
                tokens = plan.to_wire_tokens()  # Preserve the board plan's BBM order.
                if len(tokens) > self._max_ops:
                    raise HostError("PLAN_TOO_LARGE", "plan exceeds max_ops", max_ops=self._max_ops)
                crc = commit_crc(tokens)
                current = self._read_status()
                self.status = current
                if current.epoch != plan.base_epoch:
                    raise HostError("EPOCH_MISMATCH", "plan base_epoch differs from device epoch",
                                    expected=plan.base_epoch, actual=current.epoch)
                if current.epoch == 0xFFFFFFFF:
                    raise HostError("EPOCH_MISMATCH", "no uint32 epoch remains for COMMIT")
                try:
                    for op in plan.breaks:
                        if op.tag and current.shadow.tags[op.xp.chip * 128 + op.xp.x * 8 + op.xp.y] != op.tag:
                            raise JfError("BAD_ARG", "break tag differs from existing connection")
                    target = apply_ops(current.shadow, plan.breaks, plan.makes)
                except JfError as exc:
                    raise HostError("PLAN_STATE_MISMATCH", "plan preconditions differ from device shadow",
                                    cause=exc.as_dict()) from exc
                if shadow_crc(target) != plan.expect_crc:
                    raise HostError("PLAN_STATE_MISMATCH", "plan replay does not produce expect_crc")
                if self._next_id > 0xFFFFFFFF:
                    raise HostError("BAD_ARG", "transaction id space exhausted")
                transaction_id = self._next_id
                self._next_id += 1
                id_ = str(transaction_id)
                frames = self._add_frames(id_, tokens)
                phase = "staging"
                self._session.request(("TXN", "BEGIN", id_, str(plan.base_epoch), str(len(tokens))))
                for frame in frames:
                    self._session.request(frame)
                phase = "commit"
                changed = "unknown"
                committed = self._session.request(("TXN", "COMMIT", id_, str(len(frames)), f"{crc:08x}"))
                changed = "yes"
                phase = "confirmation"
                _require_no_fault(committed)
                opened, closed = _number(committed, "opened"), _number(committed, "closed")
                if (_number(committed, "epoch") != current.epoch + 1
                        or int(_hex(committed, "crc", 8), 16) != plan.expect_crc
                        or (opened, closed) != (len(plan.breaks), len(plan.makes))):
                    raise HostError("CONFIRMATION_MISMATCH", "COMMIT result differs from plan")
                status = self._read_status()
                self.status = status
                if status.epoch != current.epoch + 1 or status.crc != plan.expect_crc or status.shadow != target:
                    raise HostError("CONFIRMATION_MISMATCH", "post-COMMIT status differs from plan")
                return ApplyResult("success", transaction_id, status, opened, closed,
                                   committed.kv.get("replayed") == "1",
                                   "mock-device-applied" if self._mock else "device-reported-applied",
                                   state_changed="yes")
            except HostError as exc:
                if self._stopped is not None:
                    return self._failure(self._stopped, self._stopped_id, self._stopped_changed)
                if phase == "commit" and exc.code == "DEVICE_ERROR" and "state_changed" not in exc.details:
                    # Validation ERRs do not switch. An interrupted commit explicitly opens all.
                    changed = "yes" if exc.details.get("device_code") == "COMMIT_INTERRUPTED" else "no"
                if (phase != "precheck" or exc.code in {
                    "DEVICE_FAULT", "STATE_MISMATCH", "SHADOW_MISMATCH", "MALFORMED_RESPONSE",
                    "TIMEOUT", "TRANSPORT_ERROR", "SHORT_WRITE", "DEVICE_ERROR",
                }):
                    self._stopped, self._stopped_id, self._stopped_changed = exc, transaction_id, changed
                return self._failure(exc, transaction_id, changed)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._initialized = False
            self._session.close()
