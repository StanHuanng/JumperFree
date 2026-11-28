"""Hardware-in-the-loop check: JumperFree Host on a real JumperFree mini.

Setup (2026-11-28): ESP32-C3 SuperMini straddles columns 1..8 (USB left; A1 5V,
A2 GND, A3 3V3, B1 IO5 = SDA, B2 IO6 = SCL) running jf-c3-env firmware on its own
USB; AHT20 at A16 GND, A17 SCL, A18 SDA, A19 VCC. The C3's I2C scan is the
independent evidence: the AHT (0x38) answers only if the matrix really connects it.

Run:  python hil/mini_c3_aht.py [--mcp <command>] [--jf COM4] [--c3 COM8]
Each step prints PASS/FAIL. Nothing here is a unit test; it needs the board.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import serial

NETS = [("GND", ["A2", "A16"]), ("3V3", ["A3", "A19"]), ("SCL", ["B2", "A17"]), ("SDA", ["B1", "A18"])]
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
    return ok


class C3:
    def __init__(self, port: str) -> None:
        self.s = serial.Serial(None, 115200, timeout=0.05)
        self.s.port, self.s.dtr, self.s.rts = port, False, False  # RTS resets the C3
        self.s.open()
        time.sleep(0.2)
        self.s.read(65536)

    def ask(self, cmd: str, prefix: str, wait: float = 2.0) -> str:
        self.s.read(65536)
        self.s.write((cmd + "\n").encode())
        end, buf = time.monotonic() + wait, b""
        while time.monotonic() < end:
            buf += self.s.read(4096)
            for line in buf.decode(errors="replace").splitlines():
                if line.startswith(prefix):
                    return line.strip()
        return ""

    def scan(self) -> set[str]:
        line = self.ask("scan", "SCAN")
        return set(line.split()[1:]) if line else set()

    def stat(self) -> dict:
        line = self.ask("stat", "STAT ")
        return json.loads(line[5:]) if line else {}


class Mcp:
    def __init__(self, command: list[str]) -> None:
        self.p = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18"})

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params or {}}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def tool(self, tool: str, **arguments) -> dict:
        reply = self.rpc("tools/call", {"name": tool, "arguments": arguments})
        return reply["result"]["structuredContent"]

    def change(self, action: str, name: str, nodes: list[str] | None = None) -> dict:
        plan = self.tool("plan_net_change", action=action, name=name,
                         **({} if nodes is None else {"nodes": nodes}))
        if not plan["ok"]:
            return plan
        guide = self.tool("guide_leds", plan_id=plan["plan_id"])
        if not guide["ok"]:
            return guide
        return self.tool("execute_plan", plan_id=plan["plan_id"])

    def exit(self) -> str:
        self.p.stdin.close()
        self.p.wait(10)
        return self.p.stderr.read()

    def kill(self) -> None:
        self.p.kill()
        self.p.wait(10)


def jf_status(port: str) -> dict[str, str]:
    """Raw JF2 SW STATUS + FAULT GET on a free port (no Host running)."""
    s = serial.Serial(port, 115200, timeout=0.3)
    s.reset_input_buffer()
    out = {}
    for cmd in (b"JF2 SW STATUS\n", b"JF2 FAULT GET\n"):
        s.write(cmd)
        end, buf = time.monotonic() + 1.5, b""
        while time.monotonic() < end and not buf.endswith(b"\n"):
            buf += s.read(2048)
        out.update(t.split("=", 1) for t in buf.decode().split() if "=" in t)
    s.close()
    out["closed"] = str(any(int(out.get(f"c{c}", "0"), 16) for c in range(7)))
    return out


def wait_scan(c3: C3, want: bool, seconds: float = 4.0) -> set[str]:
    end = time.monotonic() + seconds
    found = c3.scan()
    while time.monotonic() < end and ("38" in found) != want:
        time.sleep(0.3)
        found = c3.scan()
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mcp", default="jumperfree-mcp")
    ap.add_argument("--jf", default="COM4")
    ap.add_argument("--c3", default="COM8")
    args = ap.parse_args()
    cmd = args.mcp.split()
    c3 = C3(args.c3)
    check("baseline: AHT not on the C3 bus", "38" not in c3.scan())

    # T1-T2: auto-detect, describe, route four nets in power order, C3 sees the AHT.
    host = Mcp(cmd)
    board = host.tool("describe_board")
    check("describe_board finds JumperFree mini", board.get("ok") and board["board"]["name"] == "JumperFree mini",
          f"{board.get('device')} notes={board.get('startup_notes')}")
    plan = host.tool("plan_net_change", action="create", name="GND", nodes=["A2", "A16"])
    check("execute without guide refused", host.tool("execute_plan", plan_id=plan["plan_id"])["error"]["code"]
          == "PLAN_NOT_GUIDED")
    times = []
    for name, nodes in NETS:
        t0 = time.monotonic()
        result = host.change("create", name, nodes)
        times.append(time.monotonic() - t0)
        check(f"create {name} {nodes}", result["ok"], "" if result["ok"] else str(result["error"]))
    found = wait_scan(c3, True)
    check("C3 sees AHT 0x38 through the matrix", "38" in found, f"scan={sorted(found)} "
          f"plan+guide+execute {min(times):.2f}..{max(times):.2f} s")
    time.sleep(2.5)
    stat = c3.stat()
    check("AHT reading plausible", stat.get("t") is not None and stat.get("rh") is not None,
          f"t={stat.get('t')} rh={stat.get('rh')}")

    # T3: one owner.
    second = Mcp(cmd + ["--port", args.jf] if "--port" not in cmd else cmd)
    busy = second.tool("describe_board")
    check("second Host gets PORT_BUSY", busy["error"]["code"] == "PORT_BUSY", busy["error"]["code"])
    second.exit()

    # T4: delete SDA -> AHT gone; modify back -> AHT back.
    check("delete SDA", host.change("delete", "SDA")["ok"])
    check("C3 lost the AHT after delete", "38" not in wait_scan(c3, False))
    check("create SDA again", host.change("create", "SDA", ["B1", "A18"])["ok"])
    check("C3 sees AHT again", "38" in wait_scan(c3, True))

    # T5: lease held by heartbeat for 12 s of idle.
    time.sleep(12)
    check("idle 12 s: still connected (heartbeat keeps the 5 s lease)", "38" in c3.scan())
    state = host.tool("get_state")
    check("get_state lists 4 nets", state["ok"] and len(state["netlist"]["nets"]) == 4)

    # T6: normal exit -> LEDs off + ALLOPEN.
    log = host.exit()
    check("exit log says SW ALLOPEN", "SW ALLOPEN" in log, log.strip().splitlines()[-1] if log else "")
    raw = jf_status(args.jf)
    check("after exit: all switches open, SAFE", raw["closed"] == "False" and raw.get("state") == "SAFE",
          f"state={raw.get('state')} f={raw.get('f')}")
    check("after exit: C3 lost the AHT", "38" not in wait_scan(c3, False))

    # T7: killed Host -> lease opens everything; next Host clears the latch.
    host = Mcp(cmd)
    for name, nodes in NETS:
        host.change("create", name, nodes)
    check("routed again before kill", "38" in wait_scan(c3, True))
    host.kill()
    time.sleep(6.5)
    raw = jf_status(args.jf)
    check("after kill + 6.5 s: lease opened all switches", raw["closed"] == "False" and raw.get("f") == "1",
          f"state={raw.get('state')} f={raw.get('f')}")
    check("after kill: C3 lost the AHT", "38" not in wait_scan(c3, False))
    host = Mcp(cmd)
    board = host.tool("describe_board")
    check("next Host starts and clears the latched lease fault", board.get("ok") and
          "cleared latched faults 0x1" in board.get("startup_notes", []), str(board.get("startup_notes")))
    host.exit()

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f"; failed: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
