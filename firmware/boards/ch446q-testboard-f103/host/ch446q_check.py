"""Off-board check of the CH446Q_LINK firmware: JF2 commands in real time over
a serial port (F103 USART2 PA2/PA3), and what a CH446Q on PA8/PA9/PA10 would latch.

Usage:  python host/ch446q_check.py --port COM3 [--no-swd] [--keep] [--repo <JumperFree checkout>]
        --no-swd: SWD unplugged, check the JF2 device shadow after each step instead
        --keep  : run only the two creates and leave N1/N2 closed for measuring

Every step goes through ControlService -> DeviceClient -> SerialTransport, the
MCP server's path. The firmware decodes its own waveform from the GPIO input
registers (ch446q_diag) and snapshots the decoded 8x16 latch after every change,
comparing it with the JF2 crosspoints. A hot-plug SWD access stops the UART
interrupts, so the board is reset first, all steps run over serial only, and
ch446q_diag is read over SWD once at the end; the board is reset again after.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ELF = HERE.parent / "build/CH446Q_LINK_F103.elf"
BUNDLES = Path(os.environ.get("LOCALAPPDATA", "")) / "stm32cube/bundles"
MAGIC = 0x34343651
SNAPS = 16
DIAG_FMT = f"<IIIIBBBB8HII{SNAPS * 8}H"  # ch446q_diag_t


def find_tool(name: str, bundle_glob: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    hits = sorted(BUNDLES.glob(f"{bundle_glob}/bin/{name}.exe"))
    if not hits:
        raise SystemExit(f"{name} not found (PATH or {BUNDLES / bundle_glob})")
    return str(hits[-1])


def diag_address() -> int:
    nm = find_tool("arm-none-eabi-nm", "gnu-tools-for-stm32/*")
    out = subprocess.run([nm, str(ELF)], capture_output=True, text=True, check=True).stdout
    m = re.search(r"^([0-9a-fA-F]{8}) [bBdD] ch446q_diag$", out, re.M)
    if not m:
        raise SystemExit("ch446q_diag not in ELF; rebuild the firmware")
    return int(m.group(1), 16)


class Swd:
    def __init__(self) -> None:
        self.cli = find_tool("STM32_Programmer_CLI", "programmer/*")

    def reset(self) -> None:
        subprocess.run([self.cli, "-c", "port=SWD", "mode=UR", "-rst"],
                       capture_output=True, text=True, check=True)
        time.sleep(0.5)

    def read_diag(self) -> dict:
        size = struct.calcsize(DIAG_FMT)
        res = subprocess.run([self.cli, "-c", "port=SWD", "mode=HOTPLUG", "-r32",
                              hex(diag_address()), str(size)], capture_output=True, text=True)
        words = []
        for line in res.stdout.splitlines():
            m = re.match(r"\s*0x[0-9A-Fa-f]{8}\s*:\s*((?:[0-9A-Fa-f]{8}\s*)+)$", line)
            if m:
                words += [int(w, 16) for w in m.group(1).split()]
        if len(words) * 4 < size:
            raise RuntimeError("SWD read failed:\n" + res.stdout[-800:])
        v = struct.unpack(DIAG_FMT, struct.pack(f"<{len(words)}I", *words)[:size])
        snaps = v[16]
        ring = [list(v[18 + 8 * i: 26 + 8 * i]) for i in range(SNAPS)]
        history = [ring[i % SNAPS] for i in range(max(0, snaps - SNAPS), snaps)]
        return {"magic": v[0], "strobes": v[1], "errors": v[2], "addr_pin_high": v[3],
                "latch": list(v[8:16]), "snaps": snaps, "model_errors": v[17], "history": history}


def points(latch: list[int]) -> set[tuple[int, int]]:
    return {(x, y) for y, row in enumerate(latch) for x in range(16) if row >> x & 1}


def show(latch: list[int]) -> None:
    print("        X: " + "".join(f"{x:<3d}" for x in range(16)))
    for y, row in enumerate(latch):
        print(f"       Y{y}: " + "".join(" # " if row >> x & 1 else " . " for x in range(16)))


def shadow_latch(status: dict) -> list[int]:
    """SW STATUS c0/c1 (bit = x*8 + y, little-endian) -> latch[y] bit X on the one CH446Q."""
    latch = [0] * 8
    for chip in (0, 1):
        bits = int.from_bytes(bytes.fromhex(status[f"c{chip}"]), "little")
        for b in range(128):
            if bits >> b & 1:
                latch[b % 8] |= 1 << (8 * chip + b // 8)
    return latch


def matches(latch: list[int], groups: list[set[int]]) -> bool:
    """Closed X lines are exactly the groups; each group on one Y, groups on different Ys."""
    pts = points(latch)
    if {x for x, _ in pts} != set().union(*groups):
        return False
    ys = [{y for x, y in pts if x in g} for g in groups]
    return all(len(y) == 1 for y in ys) and len(set().union(*ys)) == len(ys)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--baudrate", type=int, default=115200)
    ap.add_argument("--repo", default=str(HERE.parents[3]), help="JumperFree checkout (default: this one)")
    ap.add_argument("--no-swd", action="store_true",
                    help="SWD not wired: check the device shadow (SW STATUS) after each step instead")
    ap.add_argument("--keep", action="store_true", help="stop after the creates, leave N1 and N2 closed")
    args = ap.parse_args()
    sys.path.insert(0, str(Path(args.repo) / "python" / "src"))

    from jumperfree.board import BoardProfile
    from jumperfree.host import DeviceClient
    from jumperfree.host.transport import SerialTransport
    from jumperfree.service.control import ControlService

    class StableSerialTransport(SerialTransport):
        """Workaround: upstream readline_until() rewrites serial.timeout before every
        byte; on Windows that reconfigures the port and drops bytes."""

        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self._serial.timeout = 0.01

        def readline_until(self, deadline, *, clock=time.monotonic) -> bytes:
            line = bytearray()
            while len(line) <= 512 and clock() < deadline:
                chunk = self._serial.read(1)
                if chunk:
                    line.extend(chunk)
                    if chunk == b"\n":
                        break
            return bytes(line)

    failures: list[str] = []

    def check(label: str, cond: bool) -> None:
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")
        if not cond:
            failures.append(label)

    swd = None if args.no_swd else Swd()
    if swd:
        print("reset board (SWD), then serial only")
        swd.reset()

    steps = [
        ("create N1 [Clip3, Clip12]", lambda s: s.create("N1", ["Clip3", "Clip12"]), [{3, 12}]),
        ("create N2 [Clip0, Clip7, Clip15]", lambda s: s.create("N2", ["Clip0", "Clip7", "Clip15"]),
         [{3, 12}, {0, 7, 15}]),
        ("delete N1", lambda s: s.delete("N1"), [{0, 7, 15}]),
        ("delete N2", lambda s: s.delete("N2"), []),
    ]
    if args.keep:
        steps = steps[:2]

    profile = BoardProfile.load()
    transport = StableSerialTransport(args.port, baudrate=args.baudrate, timeout=1.0)
    client = DeviceClient(transport, topology_id=profile.topology_id,
                          board_id=profile.board_id, max_ops=profile.limits.max_ops)
    try:
        status = client.connect()
        print(f"connect: epoch={status.epoch} crc={status.crc:08x}")
        service = ControlService(client, profile)
        for label, call, groups in steps:
            t = time.perf_counter()
            r = call(service).as_dict()
            ms = 1000 * (time.perf_counter() - t)
            nets = {n["net"]: n["nodes"] for n in (r.get("netlist") or {}).get("nets", [])}
            apply = r.get("apply") or {}
            print(f"\n{label}: ok={r['ok']} {ms:.0f} ms nets={nets} "
                  f"opened={apply.get('opened', '-')} closed={apply.get('closed', '-')}")
            check("JF2 command accepted", bool(r["ok"]))
            if not r["ok"]:
                print(json.dumps(r, ensure_ascii=False, indent=1, default=str)[:3000])
            if not swd:
                latch = shadow_latch(client.read_status().as_dict())
                show(latch)
                check(f"device shadow shows {[sorted(g) for g in groups] or 'all open'}",
                      matches(latch, groups))
    finally:
        client.close()

    if not swd:
        print("\nPASS" if not failures else f"\nFAIL: {failures}")
        return 0 if not failures else 1

    d = swd.read_diag()
    print(f"\nch446q_diag over SWD: strobes={d['strobes']} errors={d['errors']} "
          f"addr_pin_high={d['addr_pin_high']} snaps={d['snaps']} model_errors={d['model_errors']}")
    check("diag magic present", d["magic"] == MAGIC)
    check("pin readback matched every frame", d["errors"] == 0)
    check("AX/AY stayed low", d["addr_pin_high"] == 0)
    check("decoded latch == JF2 crosspoints at every snapshot", d["model_errors"] == 0)
    check("history not overwritten", d["snaps"] <= SNAPS)

    expected = [("power-up all-open", [])] + [(label, groups) for label, _, groups in steps]
    history = [h for i, h in enumerate(d["history"]) if i == 0 or h != d["history"][i - 1]]
    pos = 0
    for label, groups in expected:
        while pos < len(history) and not matches(history[pos], groups):
            pos += 1
        found = pos < len(history)
        print(f"\nstate after {label}:")
        if found:
            show(history[pos])
        check(f"pins show {[sorted(g) for g in groups] or 'all open'}", found)
    check("final latch all open", not points(d["latch"]))

    print("\nreset board (SWD) to restore UART interrupts")
    swd.reset()
    print("\nPASS" if not failures else f"\nFAIL: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
