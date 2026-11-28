"""JumperFree mini slow scope: capture clips through U307 MEAS_A/MEAS_B.

Usage:
  python -E jf_scope.py <clipA> [clipB|-] [--rate HZ] [--n N] [--port COM4] [--png out.png] [--csv out.csv]

Firmware command: scope <A> [B|-] [hz] [n]. JF2 routes stay in place.
Front end: Vadc = 0.2545 * Vbus + 0.5758 * VDDA.
"""
import argparse
import sys
import time

import numpy as np
import serial

sys.stdout.reconfigure(encoding="utf-8")

K_GAIN = 0.2545
K_OFS = 0.5758


def capture(ser, a, b="-", rate=20000, n=0, timeout=10.0):
    ser.reset_input_buffer()
    ser.write(f"scope {a} {b} {rate} {n}\r".encode())
    head, rows, t0 = None, [], time.time()
    while time.time() - t0 < timeout:
        line = ser.readline().decode(errors="replace").strip()
        if not line:
            continue
        if line.startswith("SCOPE ABORT") or line.startswith("no free") or line.startswith("matrix in reset"):
            raise RuntimeError(line)
        if line.startswith("SCOPE n="):
            head = dict(kv.split("=") for kv in line.split()[1:])
            continue
        if line == "SCOPE END":
            break
        if head is not None:
            rows.append(line)
    if head is None:
        raise RuntimeError("no SCOPE header")
    raw = np.array([int(r[i:i + 3], 16) for r in rows for i in range(0, len(r), 3)], dtype=float)
    nch, vdda = int(head["ch"]), int(head["vdda"]) / 1000.0
    v = (raw * vdda / 4095.0 - K_OFS * vdda) / K_GAIN
    chans = [v[i::nch] for i in range(nch)]
    return int(head["rate"]), chans, head


def analyze(v, rate):
    lo, hi = float(np.percentile(v, 2)), float(np.percentile(v, 98))
    r = {"min": float(v.min()), "max": float(v.max()), "mean": float(v.mean()),
         "pp": float(v.max() - v.min()), "rms_ac": float(np.std(v)), "lo": lo, "hi": hi}
    swing = hi - lo
    if swing > 0.2:
        mid, hyst = (hi + lo) / 2, swing * 0.15
        state, rises, high_n = v[0] > mid, [], 0
        for i, x in enumerate(v):
            if state and x < mid - hyst:
                state = False
            elif not state and x > mid + hyst:
                state = True
                rises.append(i)
            high_n += x > mid
        if len(rises) >= 2:
            r["freq"] = rate * (len(rises) - 1) / (rises[-1] - rises[0])
            seg = v[rises[0]:rises[-1]]
            r["duty"] = float(np.mean(seg > mid)) if len(seg) else None
        r["edges"] = len(rises)
    return r


def fmt(r):
    s = f"min {r['min']:+.3f}  max {r['max']:+.3f}  mean {r['mean']:+.3f}  pp {r['pp']:.3f}  ac_rms {r['rms_ac'] * 1000:.1f} mV"
    if "freq" in r:
        s += f"  f {r['freq']:.1f} Hz  duty {r['duty'] * 100:.1f} %  rises {r['edges']}"
    return s


def plot(chans, rate, names, path, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    t = np.arange(len(chans[0])) / rate * 1000
    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=110)
    for v, nm, c in zip(chans, names, ["#1f6fb5", "#d2691e"]):
        ax.plot(t, v, lw=1, color=c, label=nm)
    ax.set_xlabel("ms")
    ax.set_ylabel("V")
    ax.grid(alpha=0.3)
    ax.legend(loc="upper right")
    ax.set_title(title or f"JF mini scope, {rate} S/s")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("b", nargs="?", default="-")
    ap.add_argument("--rate", type=int, default=20000)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--png")
    ap.add_argument("--csv")
    ap.add_argument("--title", default="")
    o = ap.parse_args()
    with serial.Serial(o.port, 115200, timeout=0.5) as ser:
        rate, chans, head = capture(ser, o.a, o.b, o.rate, o.n)
    names = [o.a] + ([o.b] if o.b != "-" else [])
    print(f"rate {rate} S/s, {len(chans[0])} samples/ch, window {len(chans[0]) / rate * 1000:.2f} ms, "
          f"busA {head['busA']} temp {head['tempA']}")
    for nm, v in zip(names, chans):
        print(f"{nm}: {fmt(analyze(v, rate))}")
    if o.png:
        plot(chans, rate, names, o.png, o.title)
        print("png", o.png)
    if o.csv:
        t = np.arange(len(chans[0])) / rate
        np.savetxt(o.csv, np.column_stack([t] + chans), delimiter=",", header="t," + ",".join(names), comments="")


if __name__ == "__main__":
    main()
