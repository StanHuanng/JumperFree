"""Real-time clip connections on JumperFree mini over JF2 (board=mini firmware 0.3).

Usage:
  python host/mini_jf2.py --port COM4                    interactive
  python host/mini_jf2.py --port COM4 c U1 U24           one command, then exit

Commands (interactive or after --port):
  c <clip> <clip> [...]   connect clips into one net (joins an existing net)
  d <clip>                remove one clip from its net
  x <clip>                remove the whole net that contains this clip
  l                       list nets read back from the device (SW STATUS / TAGS)
  clr                     SW ALLOPEN, then HELLO again
  info                    JF2 INFO
  q                       quit

Clip names: U<col> = back row (USB side), D<col> = front row, col 1..24 from the left.
Special: GND = U307 Y7 (SVC_GND, R410 to ground; keep user drive off it),
         3V3 = U307 Y6 (TEST_SRC: PB8 3.3 V through 1.5 kOhm, signal level only).
JumperFree mini routing: a net = one BUS (X line, shared by all chips) + one tag 1..15;
each clip FLIPn is Y(n%8) on chip n//8 (U301..U306) -> op +chip:bus:y@tag.
"""
from __future__ import annotations

import argparse
import re
import secrets
import shlex
import sys
import time
import zlib
from pathlib import Path

import serial

HERE = Path(__file__).resolve().parent
CLIPMAP_H = HERE.parent / "firmware/app/Inc/clipmap.h"
CHIPS, XN, YN, CLIPS = 7, 16, 8, 48
# chip 6 = U307: y6 TEST_SRC (weak 3.3 V via 1.5k), y7 SVC_GND -> same n = chip*8+y math
SPECIAL = {"3V3": 54, "GND": 55}
PALETTE = ["ff0000", "00ff00", "0000ff", "ffa000", "00ffff", "ff00ff", "ffff00", "ffffff",
           "8000ff", "0080ff", "ff4080", "80ff00", "ff6000", "00ff80", "6060ff"]  # tag 1..15


def load_clipmap() -> list[tuple[str, int, int, int]]:
    rows = re.findall(r"\{ '([UD])',\s*(\d+),\s*(\d+),\s*(\d+) \},\s*/\* FLIP(\d+) \*/", CLIPMAP_H.read_text())
    table = [None] * CLIPS
    for row, col, j, led, n in rows:
        table[int(n)] = (row, int(col), int(j), int(led))
    assert all(table), "clipmap.h incomplete"
    return table


CLIPMAP = load_clipmap()


def clip_from_name(name: str) -> int:
    if name.upper() in SPECIAL:
        return SPECIAL[name.upper()]
    m = re.fullmatch(r"([UuDd])(\d{1,2})", name)
    if m:
        row, col = m.group(1).upper(), int(m.group(2))
        for n, (r, c, _, _) in enumerate(CLIPMAP):
            if r == row and c == col:
                return n
    raise ValueError(f"bad clip '{name}' (use U1..U24 or D1..D24)")


def clip_name(n: int) -> str:
    for k, v in SPECIAL.items():
        if v == n:
            return k
    r, c, j, _ = CLIPMAP[n]
    return f"{r}{c}"


class Jf2Error(Exception):
    pass


class Device:
    def __init__(self, port: str) -> None:
        self.s = serial.Serial(port, 115200, timeout=0.02)
        self.txn_id = int(time.time()) & 0xFFFF
        self.epoch = 0

    def ask(self, cmd: str, wait: float = 1.0) -> str:
        self.s.reset_input_buffer()
        self.s.write(f"JF2 {cmd}\n".encode())
        end, buf = time.monotonic() + wait, b""
        while time.monotonic() < end:
            buf += self.s.read(1024)
            for line in buf.split(b"\n")[:-1]:
                text = line.decode(errors="replace").strip("\r> ")
                if text.startswith("JF2 "):
                    if text.startswith("JF2 ERR"):
                        raise Jf2Error(text)
                    return text
        raise Jf2Error(f"timeout waiting for reply to: JF2 {cmd}")

    @staticmethod
    def kv(line: str) -> dict[str, str]:
        return dict(t.split("=", 1) for t in line.split() if "=" in t)

    def hello(self) -> dict[str, str]:
        kv = self.kv(self.ask(f"HELLO 2 {secrets.token_hex(4)}"))
        if kv.get("board") != "mini":
            raise Jf2Error(f"board is {kv.get('board')}, need board=mini firmware")
        if kv.get("state") != "ARMED":
            raise Jf2Error(f"device not ARMED after HELLO: {kv}")
        self.epoch = int(kv["epoch"])
        return kv

    def closed(self) -> dict[tuple[int, int, int], int]:
        """(chip, x, y) -> tag for every closed crosspoint."""
        st = self.kv(self.ask("SW STATUS"))
        tg = self.kv(self.ask("SW TAGS"))
        self.epoch = int(st["epoch"])
        out = {}
        for c in range(CHIPS):
            bits = int.from_bytes(bytes.fromhex(st[f"c{c}"]), "little")
            tags = bytes.fromhex(tg[f"t{c}"])
            for b in range(XN * YN):
                if bits >> b & 1:
                    tag = (tags[b // 2] >> (4 * (b % 2))) & 15
                    out[(c, b // 8, b % 8)] = tag
        return out

    def nets(self) -> dict[int, dict]:
        """tag -> {bus, clips} from the device shadow."""
        nets: dict[int, dict] = {}
        for (c, x, y), tag in self.closed().items():
            n = nets.setdefault(tag, {"bus": x, "clips": set()})
            n["clips"].add(c * 8 + y)
        return nets

    def commit(self, ops: list[str]) -> tuple[str, float]:
        self.txn_id += 1
        tid = self.txn_id
        t0 = time.perf_counter()
        self.ask(f"TXN BEGIN {tid} {self.epoch} {len(ops)}")
        frames = [ops[i:i + 40] for i in range(0, len(ops), 40)]
        crc = 0
        for tok in ops:
            crc = zlib.crc32((tok + "\n").encode(), crc)
        try:
            for seq, chunk in enumerate(frames):
                self.ask(f"TXN ADD {tid} {seq} " + " ".join(chunk))
            reply = self.ask(f"TXN COMMIT {tid} {len(frames)} {crc:08x}", wait=2.0)
        except Jf2Error:
            # a refused ADD/COMMIT leaves the transaction open (spec): drop it
            try:
                self.ask(f"TXN ABORT {tid}")
            except Jf2Error:
                pass
            raise
        ms = (time.perf_counter() - t0) * 1000
        self.epoch = int(self.kv(reply)["epoch"])
        return reply, ms

    def leds(self, nets: dict[int, dict]) -> None:
        colour = {n: "000000" for n in range(CLIPS)}
        for tag, net in nets.items():
            for n in net["clips"] & set(range(CLIPS)):
                colour[n] = PALETTE[(tag - 1) % len(PALETTE)]
        items = " ".join(f"{n}:{colour[n]}" for n in range(CLIPS))
        self.ask(f"LED BATCH {CLIPS} {items}")


def op(clip: int, bus: int, tag: int | None) -> str:
    chip, y = clip // 8, clip % 8
    return f"+{chip}:{bus}:{y}@{tag}" if tag else f"-{chip}:{bus}:{y}"


def describe(nets: dict[int, dict]) -> str:
    if not nets:
        return "  (no nets)"
    lines = []
    for tag in sorted(nets):
        n = nets[tag]
        clips = " ".join(clip_name(c) + (f"(J{CLIPMAP[c][2]})" if c < CLIPS else "") for c in sorted(n["clips"]))
        lines.append(f"  tag {tag:2d}  BUS{n['bus']:<2d}  {clips}")
    return "\n".join(lines)


def do_connect(dev: Device, names: list[str]) -> None:
    clips = [clip_from_name(s) for s in names]
    if len(set(clips)) < 2:
        raise ValueError("need at least two different clips")
    nets = dev.nets()
    owner = {c: t for t, n in nets.items() for c in n["clips"]}
    joined = sorted({owner[c] for c in clips if c in owner})
    ops: list[str] = []
    if joined:
        tag = joined[0]
        bus = nets[tag]["bus"]
        for other in joined[1:]:  # merge: move the other nets onto this bus/tag
            for c in nets[other]["clips"]:
                ops.append(op(c, nets[other]["bus"], None))
                ops.append(op(c, bus, tag))
    else:
        free_tags = [t for t in range(1, 16) if t not in nets]
        free_bus = [b for b in range(XN) if b not in {n["bus"] for n in nets.values()}]
        if not free_tags or not free_bus:
            raise ValueError("no free net (15 tags / 16 BUS lines in use)")
        tag, bus = free_tags[0], free_bus[0]
    final = set(clips) | {c for t in joined for c in nets[t]["clips"]}
    if {54, 55} <= final:
        raise ValueError("refusing to put 3V3 (TEST_SRC) and GND in one net")
    for c in clips:
        if c not in owner:
            ops.append(op(c, bus, tag))
    reply, ms = dev.commit(ops)
    nets = dev.nets()
    dev.leds(nets)
    print(f"committed {len(ops)} op(s) in {ms:.0f} ms: {' '.join(ops)}")
    print(describe(nets))


def do_remove(dev: Device, name: str, whole: bool) -> None:
    clip = clip_from_name(name)
    nets = dev.nets()
    for tag, n in nets.items():
        if clip in n["clips"]:
            gone = n["clips"] if (whole or len(n["clips"]) <= 2) else {clip}
            ops = [op(c, n["bus"], None) for c in sorted(gone)]
            reply, ms = dev.commit(ops)
            nets = dev.nets()
            dev.leds(nets)
            print(f"committed {len(ops)} op(s) in {ms:.0f} ms: {' '.join(ops)}")
            print(describe(nets))
            return
    print(f"{name} is not in any net")


def run(dev: Device, words: list[str]) -> bool:
    if not words:
        return True
    cmd, args = words[0].lower(), words[1:]
    if cmd in ("q", "quit", "exit"):
        return False
    if cmd == "c":
        do_connect(dev, args)
    elif cmd in ("d", "x") and len(args) == 1:
        do_remove(dev, args[0], whole=(cmd == "x"))
    elif cmd == "l":
        print(describe(dev.nets()))
    elif cmd == "clr":
        dev.ask("SW ALLOPEN")
        dev.hello()
        dev.leds({})
        print("all open")
    elif cmd == "info":
        print(dev.ask("INFO"))
    else:
        print(__doc__.split("Commands")[1])
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("words", nargs="*")
    a = ap.parse_args()
    dev = Device(a.port)
    kv = dev.hello()
    print(f"HELLO ok: board={kv['board']} chips={kv['chips']} epoch={kv['epoch']} state={kv['state']}")
    print(describe(dev.nets()))
    if a.words:
        run(dev, a.words)
        return
    while True:
        try:
            line = input("jf2> ")
        except (EOFError, KeyboardInterrupt):
            break
        try:
            if not run(dev, shlex.split(line)):
                break
        except (Jf2Error, ValueError) as e:
            print("error:", e)


if __name__ == "__main__":
    main()
