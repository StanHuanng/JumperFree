"""Close or open single CH446Q crosspoints with raw JF2 commands over COM3.

Usage:  python host/jf2_raw.py --port COM3                 (print SW STATUS and closed points)
        python host/jf2_raw.py --port COM3 +0:0:0@1        (close C1 X0-Y0 -> CH446Q X0-Y0)
        python host/jf2_raw.py --port COM3 -- -0:0:0       (open it again; "--" before - tokens)
        python host/jf2_raw.py --port COM3 +0:0:1@1 -- -0:0:7   (several ops, one transaction)
        python host/jf2_raw.py --port COM3 --allopen       (open all 128 switches)
        python host/jf2_raw.py --port COM3 --raw "INFO"    (send any other JF2 command)

Token: +c:x:y@tag closes, -c:x:y opens; CH446Q X = 8*c + x, Y = y.
Closed points that share an X or Y line must use the same tag (else SAFETY_SHORT).
"""
import argparse
import re
import secrets
import time
import zlib

import serial


def ask(s: serial.Serial, cmd: str, wait: float = 1.0) -> str:
    s.reset_input_buffer()
    s.write(f"JF2 {cmd}\n".encode())
    end, buf = time.monotonic() + wait, b""
    while time.monotonic() < end and not buf.endswith(b"\n"):
        buf += s.read(256)
    line = buf.decode(errors="replace").strip()
    print(f"> JF2 {cmd}\n< {line}")
    if not line.startswith("JF2 OK"):
        raise SystemExit("device rejected the command")
    return line


def status(s: serial.Serial) -> int:
    """SW STATUS, then list closed CH446Q points. Shadow bit = x*8 + y, little-endian."""
    line = ask(s, "SW STATUS")
    closed = []
    for chip in (0, 1):
        bits = int.from_bytes(bytes.fromhex(re.search(rf"c{chip}=([0-9a-fA-F]{{32}})", line).group(1)), "little")
        closed += [f"X{8 * chip + b // 8}-Y{b % 8}" for b in range(128) if bits >> b & 1]
    print("closed:", ", ".join(closed) if closed else "none")
    return int(re.search(r"epoch=(\d+)", line).group(1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", required=True)
    ap.add_argument("--allopen", action="store_true", help="open every switch (SW ALLOPEN)")
    ap.add_argument("--raw", help="send one JF2 command as is, e.g. INFO")
    ap.add_argument("tokens", nargs="*")
    args = ap.parse_args()
    with serial.Serial(args.port, 115200, timeout=0.01) as s:
        ask(s, f"HELLO 2 {secrets.token_hex(4)}")
        epoch = status(s)
        if args.raw:
            ask(s, args.raw)
        if args.allopen:
            ask(s, "SW ALLOPEN")
            # ALLOPEN leaves the device in SAFE; a new HELLO arms it again
            ask(s, f"HELLO 2 {secrets.token_hex(4)}")
            status(s)
        if args.tokens:
            txn = int(time.time() * 1000) & 0x7FFFFFFF
            crc = zlib.crc32("".join(t + "\n" for t in args.tokens).encode())
            ask(s, f"TXN BEGIN {txn} {epoch} {len(args.tokens)}")
            ask(s, f"TXN ADD {txn} 0 {' '.join(args.tokens)}")
            ask(s, f"TXN COMMIT {txn} 1 {crc:08x}")
            status(s)


if __name__ == "__main__":
    main()
