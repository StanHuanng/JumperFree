"""JumperFree mini scope capability tests with the C3 env-station demo routed (env_demo.py route).

C3 pins: IO0 = U8 (free, PWM source), IO5 SDA = D1, IO6 SCL = D2, 3V3 = U3, 5V = U1.
AHT VCC = U10 (fed through the matrix), OLED VCC = D19.
"""
import json
import os
import sys
import time

import numpy as np
import serial

from jf_scope import analyze, capture, fmt, plot

sys.stdout.reconfigure(encoding="utf-8")
OUT = sys.argv[1] if len(sys.argv) > 1 else "scope_out"
os.makedirs(OUT, exist_ok=True)
jf = serial.Serial("COM4", 115200, timeout=0.5)
c3 = serial.Serial("COM8", 115200, timeout=0.5)
results = {}


def c3cmd(cmd):
    c3.reset_input_buffer()
    c3.write((cmd + "\n").encode())
    t0 = time.time()
    while time.time() - t0 < 2:
        line = c3.readline().decode(errors="replace").strip()
        if line.startswith(("OK", "ERR")):
            return line
    return "(no reply)"


def shot(name, a, b="-", rate=20000, n=0, title=""):
    r, ch, head = capture(jf, a, b, rate, n)
    names = [a] + ([b] if b != "-" else [])
    stats = [analyze(v, r) for v in ch]
    plot(ch, r, names, os.path.join(OUT, name + ".png"), title or name)
    print(f"[{name}] rate {r}, {len(ch[0])} pts, {len(ch[0]) / r * 1000:.2f} ms, busA {head['busA']} temp {head['tempA']}")
    for nm, s in zip(names, stats):
        print(f"   {nm}: {fmt(s)}")
    results[name] = {"rate": r, "n": len(ch[0]), "stats": dict(zip(names, stats))}
    return r, ch, stats


# 1. PWM frequency sweep on IO0 / U8: amplitude and frequency accuracy
print("== 1. PWM sweep (C3 IO0 -> clip U8, temp BUS) ==")
sweep = []
for f in [100, 300, 1000, 3000, 6000, 10000, 20000, 50000, 100000]:
    print(c3cmd(f"pwm 0 {f} 50"))
    time.sleep(0.2)
    rate = min(500000, max(20000, f * 40))
    r, ch, st = shot(f"pwm_{f}", "U8", rate=rate)
    s = st[0]
    sweep.append({"f": f, "rate": r, "lo": s["lo"], "hi": s["hi"], "swing": s["hi"] - s["lo"],
                  "f_meas": s.get("freq"), "duty": s.get("duty")})
print(c3cmd("pwm 0 1000 25"))
time.sleep(0.2)
shot("pwm_1k_duty25", "U8", rate=100000)
print(c3cmd("pwm 0 off"))
results["sweep"] = sweep

# 2. I2C bus SCL/SDA (C3 polls the sensors; catch a burst)
print("== 2. I2C SCL D2 / SDA D1 ==")
best = None
for i in range(12):
    r, ch, st = capture(jf, "D2", "D1", 250000, 0)
    act = float(np.mean(ch[0] < 1.5))
    if best is None or act > best[0]:
        best = (act, r, ch)
    if act > 0.05:
        break
act, r, ch = best
plot(ch, r, ["SCL D2", "SDA D1"], os.path.join(OUT, "i2c.png"), f"I2C burst, {r} S/s per ch")
results["i2c"] = {"rate": r, "low_fraction_scl": act, "stats": [analyze(v, r) for v in ch]}
print(f"[i2c] rate {r}, SCL low fraction {act:.3f}", [fmt(analyze(v, r)) for v in ch])
shot("i2c_idle_levels", "D2", "D1", 20000)

# 3. Rails: C3 3V3 (U3) vs AHT VCC via matrix (U10); OLED VCC (D19); 5V (U1)
print("== 3. Rails ==")
shot("rail_3v3_vs_aht", "U3", "U10", 20000)
shot("rail_3v3_vs_aht_fast", "U3", "U10", 250000)
shot("rail_oled_vcc", "D19", "-", 100000)
shot("rail_5v", "U1", "-", 20000)
shot("rail_3v3_slow", "U3", "-", 2000)  # 512 ms window: Wi-Fi beacons every ~102 ms

with open(os.path.join(OUT, "results.json"), "w", encoding="utf-8") as fh:
    json.dump(results, fh, indent=1, default=float)
print("saved", OUT)
