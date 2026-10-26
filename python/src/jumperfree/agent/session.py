"""Own one empty device (MockDevice or serial) and its confirmed intent until explicitly closed."""

from __future__ import annotations

from typing import Callable

from ..board import BoardProfile
from ..core import JfError
from ..host import DeviceClient, HostError
from ..host.mock_device import MockDevice
from ..host.transport import SerialTransport, Transport
from ..service.control import ControlResult, ControlService


def _validation_error(operation: str, name: str, nodes: list[str] | None = None,
                      *, check_nodes: bool = False) -> dict[str, object] | None:
    # Reuse existing error codes. Semantic node/routing checks belong to ControlService.
    error = None
    if not isinstance(name, str) or not name.strip():
        error = HostError("INVALID_NET_ID", "net id must be a non-empty string")
    elif check_nodes and (not isinstance(nodes, list)
                          or not all(isinstance(node, str) for node in nodes)):
        error = HostError("BAD_ARG", "nodes must be an array of strings")
    if error is not None:
        return ControlResult(False, operation, error=error).as_dict()
    return None


MOCK_DEVICE_LABEL = "MockDevice"


class AgentSession:
    """Single-owner session over one transport; process exit loses all intent.

    Use as a context manager or close in finally. No recovery or write-gate
    acknowledgment is exposed. ``device_label`` is reported verbatim in every
    result's ``device`` field; ``verified`` is always False.
    """

    def __init__(self, transport: Transport, device_label: str) -> None:
        self._device_label = device_label
        try:
            self._profile = BoardProfile.load()
        except BaseException:
            transport.close()
            raise
        self._device = transport
        self._client = DeviceClient(
            self._device, topology_id=self._profile.topology_id,
            board_id=self._profile.board_id, max_ops=self._profile.limits.max_ops,
            retries=0,
        )
        self._closed = False
        try:
            self._client.connect()
            self._control = ControlService(self._client, self._profile)
        except BaseException as exc:
            self._close_with_error(exc)
            raise

    def _invoke(self, operation: str, *args: object) -> dict[str, object]:
        data = self._invoke_raw(operation, *args)
        data["device"] = self._device_label
        return data

    def _invoke_raw(self, operation: str, *args: object) -> dict[str, object]:
        if self._closed:
            return ControlResult(False, operation, error=HostError(
                "SESSION_CLOSED", "agent session is closed",
            )).as_dict()
        if operation == "plan":
            action, name, nodes = args
            if not isinstance(action, str) or action not in {"create", "modify", "delete"}:
                return ControlResult(False, "plan", error=HostError(
                    "BAD_ARG", "action must be create, modify or delete")).as_dict()
            failure = _validation_error(
                "plan", name, nodes, check_nodes=action in {"create", "modify"})
            if failure is not None:
                return failure
            return self._control.plan(action, name, nodes).as_dict()
        if operation not in {"query", "guide_leds", "all_open"}:
            failure = _validation_error(
                operation, *args, check_nodes=operation in {"create", "modify"},
            )
            if failure is not None:
                return failure
        return getattr(self._control, operation)(*args).as_dict()

    def close(self) -> None:
        """Close the client once; never clear device nets, faults or stop state."""
        if not self._closed:
            self._closed = True
            self._client.close()

    def _close_with_error(self, primary_error: BaseException | None) -> None:
        try:
            self.close()
        except HostError as exc:
            if primary_error is None:
                raise
            if isinstance(primary_error, JfError):
                primary_error.details["close_error"] = exc.as_dict()

    def __enter__(self):
        if self._closed:
            raise HostError("SESSION_CLOSED", "agent session is closed")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._close_with_error(exc_value)


class MockAgentSession(AgentSession):
    """In-memory session over a fresh MockDevice; never touches real hardware."""

    def __init__(self) -> None:
        super().__init__(MockDevice(), MOCK_DEVICE_LABEL)


def open_session(mode: str = "mock", port: str | None = None, *,
                 baudrate: int | None = None, timeout: float | None = None,
                 serial_factory: Callable[..., Transport] = SerialTransport) -> AgentSession:
    """Session factory: ``mock`` or ``serial`` (requires ``port``).

    pyserial is imported lazily inside SerialTransport, so the mock path works
    without it. ``serial_factory`` lets tests inject a fake transport.
    """
    if mode == "mock":
        if port is not None or baudrate is not None or timeout is not None:
            raise HostError("INVALID_SERIAL_CONFIG", "port, baudrate and timeout cannot be used with mock")
        return MockAgentSession()
    if mode == "serial":
        if not isinstance(port, str) or not port.strip():
            raise HostError("INVALID_SERIAL_CONFIG", "serial mode requires a non-blank port")
        kwargs = {k: v for k, v in (("baudrate", baudrate), ("timeout", timeout)) if v is not None}
        return AgentSession(serial_factory(port, **kwargs), f"serial:{port}")
    raise HostError("BAD_ARG", "mode must be mock or serial")
