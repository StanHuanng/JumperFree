"""I2C timing with the v0.4 one-channel edge capture (PB8 via U307 Y6).

PB8 sees one line at a time, so SCL and SDA are captured in separate runs, both
triggered by `la <clip> i` (first falling edge after >= 50 us idle high):
  SDA run: t = 0 is the START (SDA falls while SCL is high)
  SCL run: t = 0 is the first SCL fall (START + tHD;STA)
The SCL pattern of a hardware master is the same for every transaction, so an
SDA run is decoded against an SCL run after a shift d that puts every SDA change
(except START/STOP) inside an SCL low phase.

Usage: python -E la_i2c.py [--scl D2] [--sda D1] [--runs 6] [--win 400] [--png out.png]
"""
import argparse
import sys
import time

import numpy as np
import serial

sys.stdout.reconfigure(encoding="utf-8")
F_CPU = 64e6


HW = False  # True: use `lac` (TIM4 input capture, 15.6 ns)


def la(ser, clip, trig="i", wait_ms=1000, win_us=400):
    if HW:
        from lac import lac
        l0, t, rise, h = lac(ser, clip, trig, wait_ms, win_us)
        h["loop_ns"] = "15.6 (TIM4 capture), lost=" + h["lost"]
        return l0, t, h
    ser.reset_input_buffer()
    ser.write(f"la {clip} {trig} {wait_ms} {win_us}\r".encode())
    head, ts, t0 = None, [], time.time()
    while time.time() - t0 < wait_ms / 1000 + 3:
        line = ser.readline().decode(errors="replace").strip()
        if not line:
            continue
        if line.startswith(("LA NOTRIG", "TEST_SRC", "no free", "matrix in reset")):
            raise RuntimeError(line)
        if line.startswith("LA n="):
            head = dict(kv.split("=") for kv in line.split()[1:])
        elif line == "LA END":
            break
        elif head is not None:
            ts += [int(x, 16) / F_CPU * 1e6 for x in line.split()]
    if head is None:
        raise RuntimeError("no LA header")
    return int(head["level0"]), np.array(ts), head


def level_at(level0, edges, t):
    """Line level at time t (us); edges[0] = trigger edge at 0."""
    k = np.searchsorted(edges, t, side="right")  # edges at or before t
    return level0 ^ (k & 1)


def align(scl, sda, d_max=None, step=0.05):
    """Shift d (us) of the SCL run relative to the SDA run (SDA START = 0)."""
    l0c, ec = scl
    l0d, ed = sda
    if d_max is None:  # less than one SCL period, so the shift cannot slip by a whole clock
        d_max = 0.9 * float(np.median(np.diff(ec[0::2]))) if len(ec) > 4 else 20.0
    best = None
    for d in np.arange(0.0, d_max, step):
        e = ec + d
        bad, margin = 0, 1e9
        for t in ed[1:]:
            if t > e[-1]:
                break
            if level_at(l0c, e, t) == 1:  # SDA changed while SCL high
                bad += 1
            else:
                k = np.searchsorted(e, t)
                margin = min(margin, t - e[k - 1], e[k] - t if k < len(e) else 1e9)
        key = (bad, -margin)
        if best is None or key < best[0]:
            best = (key, d)
    return best[1], best[0][0]


def decode(scl, sda, d):
    l0c, ec = scl
    l0d, ed = sda
    e = ec + d
    rises = [t for i, t in enumerate(e) if (l0c ^ ((i + 1) & 1)) == 1]  # edges that go high
    bits = [int(level_at(l0d, ed, t)) for t in rises]
    out = []
    for i in range(0, len(bits) - 8, 9):
        b = bits[i:i + 9]
        out.append((int("".join(map(str, b[:8])), 2), "ACK" if b[8] == 0 else "NAK"))
    # timing (us): data setup to SCL rise, hold after SCL fall
    falls = [t for i, t in enumerate(e) if (l0c ^ ((i + 1) & 1)) == 0]
    su, hd = [], []
    for t in ed[1:]:
        if t > e[-1]:
            break
        r = [x for x in rises if x > t]
        f = [x for x in falls if x < t]
        if r:
            su.append(r[0] - t)
        if f:
            hd.append(t - f[-1])
    return out, rises, su, hd


def scl_timing(scl):
    l0c, ec = scl
    d = np.diff(ec)
    lows = d[0::2] if l0c == 1 else d[1::2]   # first edge is a fall when idle-high
    highs = d[1::2] if l0c == 1 else d[0::2]
    per = lows[: len(highs)] + highs[: len(lows)]
    return {"f_khz": 1000 / np.median(per), "t_low": float(np.median(lows)), "t_high": float(np.median(highs)),
            "jitter": float(np.max(per) - np.min(per)), "periods": len(per)}


def plot(scl, sda, d, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    l0c, ec = scl
    l0d, ed = sda
    t = np.arange(-5, min(ec[-1] + d, ed[-1]) + 5, 0.05)
    fig, ax = plt.subplots(figsize=(11, 2.8), dpi=110)
    ax.step(t, level_at(l0c, ec + d, t) + 1.4, where="post", color="#1f6fb5", lw=1)
    ax.step(t, level_at(l0d, ed, t), where="post", color="#d2691e", lw=1)
    ax.set_yticks([0.5, 1.9])
    ax.set_yticklabels(["SDA", "SCL"])
    ax.set_xlabel("us after START")
    ax.set_title(title)
    ax.grid(alpha=0.3, axis="x")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM4")
    ap.add_argument("--scl", default="D2")
    ap.add_argument("--sda", default="D1")
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--win", type=int, default=400)
    ap.add_argument("--png")
    ap.add_argument("--hw", action="store_true", help="use lac (TIM4 input capture)")
    o = ap.parse_args()
    global HW
    HW = o.hw
    with serial.Serial(o.port, 115200, timeout=0.5) as ser:
        # SCL: keep the longest of 4 runs (short ones are address-only probes)
        best = None
        for _ in range(4):
            l0, ec, h = la(ser, o.scl, "i", 1000, o.win)
            if best is None or len(ec) > len(best[1]):
                best = (l0, ec, h)
        l0, ec, h = best
        scl = (l0, ec)
        print(f"SCL run: {len(ec)} edges, time resolution {h['loop_ns']}")
        tm = scl_timing(scl)
        print(f"  f_SCL {tm['f_khz']:.1f} kHz, tLOW {tm['t_low']:.2f} us, tHIGH {tm['t_high']:.2f} us, "
              f"period spread {tm['jitter']:.2f} us over {tm['periods']} periods")
        runs = []
        for r in range(o.runs):
            l0d, ed, _ = la(ser, o.sda, "i", 1000, o.win)
            sda = (l0d, ed)
            d, bad = align(scl, sda)
            runs.append((sda, d, bad))
            time.sleep(0.05)
    # One master = one tHD;STA: use the most common shift of the clean runs,
    # because a shift by one SCL high phase can also give zero violations.
    ds = [round(d, 1) for _, d, bad in runs if bad == 0]
    d_fix = max(set(ds), key=ds.count) if ds else runs[0][1]
    print(f"tHD;STA (most common shift) {d_fix:.2f} us, from {ds.count(d_fix)}/{len(runs)} runs")
    first = True
    for r, (sda, d, _) in enumerate(runs):
        data, rises, su, hd = decode(scl, sda, d_fix)
        e = scl[1] + d_fix
        viol = sum(1 for t in sda[1][1:] if t <= e[-1] and level_at(scl[0], e, t) == 1)
        txt = []
        for i, (v, a) in enumerate(data):
            txt.append(f"addr 0x{v >> 1:02X} {'R' if v & 1 else 'W'} {a}" if i == 0 else f"0x{v:02X} {a}")
        ok = viol == 0
        print(f"run {r}: tSU;DAT min {min(su) if su else float('nan'):.2f} us, tHD;DAT min "
              f"{min(hd) if hd else float('nan'):.2f} us  " + " | ".join(txt) +
              ("" if ok else f"   <- {viol} SDA changes while SCL high: other transaction type, not valid"))
        if o.png and ok and first:
            plot(scl, sda, d_fix, o.png, "I2C via JF v0.4 PB8 capture: " + " | ".join(txt[:4]))
            first = False


if __name__ == "__main__":
    main()
