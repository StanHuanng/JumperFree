"""JF mini hardware edge capture (`lac`, TIM4 input capture on PB8, 15.6 ns).

Library + CLI:
  python -E lac.py pwm  <clip> [--win 2000]           frequency, duty, pulse widths, jitter
  python -E lac.py uart <clip> [--win 20000]          auto baud + 8N1 decode (idle-high line)
  python -E lac.py raw  <clip> [--trig f] [--win ..]  print edges
"""
import argparse
import sys
import time

import numpy as np
import serial

sys.stdout.reconfigure(encoding="utf-8")
TICK_US = 1 / 64.0


def lac(ser, clip, trig="f", wait_ms=1000, win_us=2000, before=None):
    """Returns (level0, edge times us, rising flags, header). `before` runs after the command is sent."""
    ser.reset_input_buffer()
    ser.write(f"lac {clip} {trig} {wait_ms} {win_us}\r".encode())
    if before:
        time.sleep(0.05)
        before()
    head, vals, t0 = None, [], time.time()
    while time.time() - t0 < wait_ms / 1000 + win_us / 1e6 + 3:
        line = ser.readline().decode(errors="replace").strip()
        if not line:
            continue
        if line.startswith(("LA NOTRIG", "TEST_SRC", "no free", "matrix in reset")):
            raise RuntimeError(line)
        if line.startswith("LAC n="):
            head = dict(kv.split("=") for kv in line.split()[1:])
        elif line == "LA END":
            break
        elif head is not None:
            vals += [int(x, 16) for x in line.split()]
    if head is None:
        raise RuntimeError("no LAC header")
    v = np.array(vals, dtype=np.int64)
    rise = (v & 1).astype(int)
    t = (v >> 1) * TICK_US
    return 1 - rise[0], t, rise, head


def pwm_stats(t, rise):
    tr, tf = t[rise == 1], t[rise == 0]
    per = np.diff(tr) if len(tr) > 2 else np.diff(tf)
    highs = [f - r for r in tr for f in tf[tf > r][:1]]
    if len(per) == 0:
        return None
    f = 1e6 / np.mean(per)
    duty = np.mean(highs) / np.mean(per) if highs else float("nan")
    return {"f_hz": f, "duty": duty, "high_us": float(np.mean(highs)) if highs else float("nan"),
            "high_min": float(np.min(highs)) if highs else float("nan"),
            "high_max": float(np.max(highs)) if highs else float("nan"),
            "period_us": float(np.mean(per)), "jitter_ns": float((np.max(per) - np.min(per)) * 1000), "n": len(per)}


def uart_decode(t, rise, level0=1):
    """8N1, idle high. Bit time = shortest pulse, refined over the frame."""
    if len(t) < 2:
        return None, ""
    w = np.diff(t)
    bit = np.min(w[w > 0.05])
    # refine: every pulse is an integer number of bits
    k = np.maximum(1, np.round(w / bit))
    bit = float(np.sum(w) / np.sum(k))
    def lvl(x):
        i = np.searchsorted(t, x, side="right")
        return level0 ^ (i & 1)
    out, i_t = bytearray(), 0
    edges = list(t)
    pos = 0
    while pos < len(edges):
        s = edges[pos]
        if lvl(s + bit * 0.5) != 0:   # not a start bit
            pos += 1
            continue
        b = 0
        for j in range(8):
            b |= lvl(s + bit * (1.5 + j)) << j
        stop = lvl(s + bit * 9.5)
        out.append(b)
        nxt = s + bit * 9.6
        pos = int(np.searchsorted(t, nxt))
        while pos < len(edges) and lvl(edges[pos] + 1e-3) != 0:
            pos += 1
        if not stop:
            out += b"?"
    return 1e6 / bit, out.decode(errors="replace")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pwm", "uart", "raw"])
    ap.add_argument("clip")
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--trig", default=None)
    ap.add_argument("--win", type=int, default=None)
    o = ap.parse_args()
    trig = o.trig or {"pwm": "r", "uart": "i", "raw": "f"}[o.mode]
    win = o.win or {"pwm": 2000, "uart": 20000, "raw": 2000}[o.mode]
    with serial.Serial(o.port, 115200, timeout=0.5) as ser:
        l0, t, rise, h = lac(ser, o.clip, trig, 2000, win)
    print(f"{len(t)} edges, lost {h['lost']}, full {h['full']}")
    if o.mode == "pwm":
        print(pwm_stats(t, rise))
    elif o.mode == "uart":
        print(uart_decode(t, rise, l0))
    else:
        for a, b in zip(t, rise):
            print(f"{a:10.4f} {'R' if b else 'F'}")


if __name__ == "__main__":
    main()
