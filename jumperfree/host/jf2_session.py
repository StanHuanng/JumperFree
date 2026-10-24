"""JF2 单请求会话：总 deadline、原样重试与 COMMIT 查询恢复。"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Iterable

from .errors import HostError
from .jf2 import Jf2Response, encode_jf2, parse_jf2_response
from .transport import Transport


class Jf2Session:
    def __init__(self, transport: Transport, *, clock: Callable[[], float] = time.monotonic,
                 frame_timeout: float = 0.3, retries: int = 2) -> None:
        if (not isinstance(frame_timeout, (int, float)) or isinstance(frame_timeout, bool)
                or not math.isfinite(frame_timeout) or frame_timeout <= 0
                or type(retries) is not int or retries < 0):
            raise HostError("BAD_ARG", "invalid timeout or retries")
        self.transport = transport
        self.clock = clock
        self.frame_timeout = frame_timeout
        self.retries = retries
        self._lock = threading.Lock()
        self._closed = False
        self._ops: dict[str, int] = {}

    def request(self, cmd_tokens: Iterable[str], *, n_ops: int | None = None) -> Jf2Response:
        if isinstance(cmd_tokens, (str, bytes)):
            raise HostError("BAD_FRAME", "expected command tokens")
        tokens = tuple(cmd_tokens)
        frame = encode_jf2(tokens)
        with self._lock:
            if self._closed:
                raise HostError("SESSION_CLOSED", "session is closed")
            try:
                if tokens[:2] == ("TXN", "COMMIT"):
                    count = self._ops.get(tokens[2]) if n_ops is None else n_ops
                    if type(count) is not int or not 0 <= count <= 256:
                        raise HostError("BAD_ARG", "COMMIT requires known n_ops in 0..256")
                    try:
                        return self._exchange(tokens, frame, 1 + 0.005 * count, 0)
                    except HostError as exc:
                        if exc.code != "TIMEOUT":
                            raise
                        return self._resolve_commit(tokens[2])
                response = self._exchange(tokens, frame, self.frame_timeout, self.retries)
                if tokens[:2] == ("TXN", "BEGIN"):
                    self._ops[tokens[2]] = int(tokens[4])
                return response
            except OSError as exc:
                raise HostError("TRANSPORT_ERROR", str(exc), operation=" ".join(tokens[:2])) from exc

    def _exchange(self, tokens: tuple[str, ...], frame: bytes, timeout: float, retries: int) -> Jf2Response:
        cmd_len = 2 if tokens[0] in {"TXN", "SW", "LED", "FAULT", "CFG"} else 1
        cmd = tokens[:cmd_len]
        echoes = {
            ("PING",): 1, ("LEASE",): 1, ("SELFTEST",): 1,
            ("TXN", "BEGIN"): 1, ("TXN", "ADD"): 2, ("TXN", "COMMIT"): 1,
            ("TXN", "ABORT"): 1, ("TXN", "QUERY"): 1,
            ("CFG", "GET"): 1, ("LED", "BATCH"): 1,
        }.get(cmd, 0)
        expected = tokens[cmd_len:cmd_len + echoes]
        for _ in range(retries + 1):
            reset = getattr(self.transport, "reset_input", None)
            if reset is not None:
                reset()
            deadline = self.clock() + timeout
            if self.transport.write(frame) != len(frame):
                raise HostError("SHORT_WRITE", "transport did not write entire request")
            while self.clock() < deadline:
                bounded_read = getattr(self.transport, "readline_with_timeout", None)
                line = (bounded_read(max(0.0, deadline - self.clock())) if bounded_read is not None
                        else self.transport.readline())
                if self.clock() >= deadline:
                    break
                try:
                    response = parse_jf2_response(line)
                except HostError as exc:
                    if exc.code == "DEVICE_ERROR":
                        raise
                    continue
                if response.cmd != cmd or response.args != expected:
                    continue
                if cmd == ("HELLO",) and response.kv.get("nonce") != tokens[2]:
                    continue
                return response
        raise HostError("TIMEOUT", "no matching response before deadline")

    def _resolve_commit(self, txn_id: str) -> Jf2Response:
        tokens = ("TXN", "QUERY", txn_id)
        try:
            result = self._exchange(tokens, encode_jf2(tokens), self.frame_timeout, self.retries)
        except (HostError, OSError) as exc:
            code = "TRANSPORT_ERROR" if isinstance(exc, OSError) else exc.code
            details = dict(exc.details) if isinstance(exc, HostError) else {}
            details["state_changed"] = "unknown"
            raise HostError(code, str(exc), **details) from exc
        state = result.kv.get("result")
        if state == "COMMITTED" and all(key in result.kv for key in ("epoch", "crc", "opened", "closed")):
            info = {key: value for key, value in result.kv.items() if key != "result"}
            info["replayed"] = "1"
            return Jf2Response(True, ("TXN", "COMMIT"), (txn_id,), info)
        raise HostError("COMMIT_UNRESOLVED", "COMMIT result is " + str(state),
                        state_changed="no" if state == "ABORTED" else "unknown", result=state)

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                try:
                    self.transport.close()
                except OSError as exc:
                    raise HostError("TRANSPORT_ERROR", str(exc), operation="close") from exc
