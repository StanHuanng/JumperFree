"""JumperFree v0.4 user-flow demo, zero jumper wires:
律动小板 (STM32C532CCT6, 2 x 20 pins, 600 mil) straddles the channel like a DIP,
0.96" SSD1306 OLED lies flat in the front row's outermost holes.

  python host/oled_demo.py guide     light where things go
  python host/oled_demo.py check     find every pin that matters, by measurement
  python host/oled_demo.py route     route SCL/SDA through the matrix, start the OLED
  python host/oled_demo.py press [s] JF "presses" PA0 (joins it to a GND clip) for s s
  python host/oled_demo.py clear     all switches open, LEDs off

Planned layout (board pins 1 at the right, J2 = 3V3/GND/5V header in the front row):
  column 21 - k holds J1 pin k (back row) and J2 pin k (front row), k = 1..20
  J2-1 3V3 -> D20, J2-2 GND -> D19, J2-3 5V -> D18, J2-7 PB6 -> D14, J2-6 PB7 -> D15
  OLED GND/VCC share D19/D20 with the board's GND/3V3 pins (spring contacts carry
  the current, never the matrix); OLED SCL -> D21, SDA -> D22 (own clips).

The check does not trust the plan: the 律动小板 firmware (07_OLED_DEMO) answers
`id none|pb6|pb7|pa0` by pulling one pin low; JF measures all 48 clips with MEAS_A
and sees which clip follows. Rails and pull-ups are told apart with an impedance
probe (TEST_SRC 1.5 kOhm step). Routing uses the measured positions; power and
other driven clips are never put in a net.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import serial

import v04_jf2 as jf

JF_PORT = os.environ.get("JF_PORT", "COM4")
CCT_PORT = os.environ.get("CCT_PORT", "COM3")
STATE = Path(__file__).with_name("oled_demo_state.json")

COLOURS = {"blue": "0000ff", "red": "ff0000", "yellow": "ffb000", "green": "00ff00",
           "purple": "c000ff", "white": "ffffff", "orange": "ff3000", "off": "000000"}
CN = {"blue": "蓝", "red": "红", "yellow": "黄", "green": "绿", "purple": "紫", "white": "白", "orange": "橙"}

GUIDE = [
    ("U1",  "white",  "律动小板左端（后排）"),
    ("D1",  "white",  "律动小板左端（前排）"),
    ("D14", "yellow", "小板 PB6（J2-7），会接到 OLED SCL"),
    ("D15", "green",  "小板 PB7（J2-6），会接到 OLED SDA"),
    ("U16", "purple", "小板 PA0（J1-5），JF 用它当按键（位置以实测为准）"),
    ("D19", "blue",   "小板 GND（J2-2）+ OLED GND"),
    ("D20", "red",    "小板 3V3（J2-1）+ OLED VCC，小板右端"),
    ("D21", "yellow", "OLED SCL"),
    ("D22", "green",  "OLED SDA"),
]
PLAN = {"pb6": "D14", "pb7": "D15", "pa0": "U16", "gnd": "D19", "3v3": "D20",
        "oled_scl": "D21", "oled_sda": "D22"}
ALL_CLIPS = [f"{r}{c}" for r in "UD" for c in range(1, 25)]


# ----------------------------------------------------------------- serial

def console(s: serial.Serial, cmd: str, wait: float = 1.5) -> str:
    """One console line on the JF board; returns its output up to the prompt."""
    s.reset_input_buffer()
    s.write((cmd + "\r\n").encode())
    end, buf = time.monotonic() + wait, ""
    while time.monotonic() < end:
        buf += s.read(2048).decode(errors="replace")
        body = buf.split("\n", 1)[1] if "\n" in buf else ""
        if body.rstrip().endswith(">"):
            break
    return buf


class Cct:
    """律动小板 demo console (ST-Link VCP)."""

    def __init__(self, port: str = CCT_PORT) -> None:
        self.s = serial.Serial(port, 115200, timeout=0.05)
        time.sleep(0.05)

    def ask(self, cmd: str, wait: float = 0.6, want: str = "") -> str:
        self.s.write((cmd + "\r\n").encode())
        end, buf = time.monotonic() + wait, ""
        while time.monotonic() < end:
            buf += self.s.read(2048).decode(errors="replace")
            if want and want in buf:
                time.sleep(0.05)
                buf += self.s.read(2048).decode(errors="replace")
                break
        return buf

    def listen(self, seconds: float) -> str:
        end, buf = time.monotonic() + seconds, ""
        while time.monotonic() < end:
            buf += self.s.read(2048).decode(errors="replace")
        return buf


def paint(dev: jf.Device, colour_of: dict[str, str]) -> None:
    items = [f"{jf.clip_from_name(n)}:{COLOURS[colour_of.get(n, 'off')]}" for n in ALL_CLIPS]
    dev.ask(f"LED BATCH {len(items)} " + " ".join(items))


def jf_session() -> jf.Device:
    dev = jf.Device(JF_PORT)
    dev.hello()
    return dev


# ----------------------------------------------------------------- measure

def meas(s: serial.Serial, clip: str) -> float:
    out = console(s, f"meas {clip}")
    m = re.search(r"via BUS\d+: (-?\d+\.\d+) V", out)
    if not m:
        raise RuntimeError(f"meas {clip}: {out!r}")
    return float(m.group(1))


def probe_d(s: serial.Serial, clip: str) -> int:
    """TEST_SRC (1.5k) and MEAS_A on BUS0 with this clip only.
    d (ADC mV) ~ 840 open, ~0 on a rail or driven pin, a few hundred via a pull-up."""
    n = jf.clip_from_name(clip)
    console(s, "clr")
    for cmd in ("sw 7 0 6 1", "sw 7 0 4 1", f"sw {n // 8 + 1} 0 {n % 8} 1"):
        console(s, cmd)
    out = console(s, "probe")
    console(s, "clr")
    m = re.search(r"d (-?\d+) mV", out)
    if not m:
        raise RuntimeError(f"probe {clip}: {out!r}")
    return int(m.group(1))


def col_shift(found: str, planned: str) -> str:
    if found[0] != planned[0]:
        return "排不对（前/后排反了）"
    d = int(found[1:]) - int(planned[1:])
    return "位置对" if d == 0 else f"往{'右' if d > 0 else '左'}偏了 {abs(d)} 列"


# ----------------------------------------------------------------- steps

def guide() -> None:
    dev = jf_session()
    dev.ask("SW ALLOPEN")
    dev.hello()
    paint(dev, {c: col for c, col, _ in GUIDE})
    for c, col, what in GUIDE:
        print(f"  {CN[col]}  {'后排' if c[0] == 'U' else '前排'}第 {c[1:]:>2} 列  {what}")


def matrix_health(s: serial.Serial) -> str:
    """'' if OK. Checks +5V and the U307 service path (TEST_SRC -> BUS0 -> MEAS_A),
    which does not touch the breadboard: d ~ 840 when the switches work."""
    out = console(s, "adc")
    m = re.search(r"\+5V\s+(-?\d+\.\d+) V", out)
    v5 = float(m.group(1)) if m else 0.0
    console(s, "clr")
    console(s, "sw 7 0 6 1")
    console(s, "sw 7 0 4 1")
    pm = re.search(r"d (-?\d+) mV", console(s, "probe"))
    console(s, "clr")
    d = int(pm.group(1)) if pm else 0
    bad = []
    if v5 < 4.95:
        bad.append(f"+5V 只有 {v5:.2f} V（正常约 5.05 V）")
    if d < 600:
        bad.append(f"U307 服务通路 d = {d} mV（正常约 840 mV）")
    return "；".join(bad)


def check() -> bool:
    try:
        cct = Cct()
    except serial.SerialException:
        print(f"电脑上没有律动小板的串口 {CCT_PORT}：小板 USB 没接到电脑，或线只能充电。")
        return False
    dev = jf.Device(JF_PORT)
    s = dev.s
    if "OK id none" not in cct.ask("id none", want="OK"):
        print("律动小板没有回应（COM3）：USB 插好了吗？")
        return False
    bad = matrix_health(s)
    if bad:
        print("JF 矩阵自身不正常，先不判断接线：" + bad)
        print("可能闩锁：先拔小板 USB，再拔 JF，空板上电跑 selftest。")
        return False
    t0 = time.monotonic()
    base = {c: meas(s, c) for c in ALL_CLIPS}
    high = [c for c, v in base.items() if 2.9 < v < 3.7]
    phase = {}
    for pin in ("pb6", "pb7", "pa0"):
        cct.ask(f"id {pin}", want="OK")
        time.sleep(0.05)
        phase[pin] = {c: meas(s, c) for c in high}
    cct.ask("id none", want="OK")
    # a pin is where the clip is low only in its own phase
    found = {}
    for pin in phase:
        hits = [c for c in high if phase[pin][c] < 0.8 and
                all(phase[o][c] > 2.5 for o in phase if o != pin)]
        found[pin] = hits
    pinned = {c for v in found.values() for c in v}
    dz = {c: probe_d(s, c) for c, v in base.items()
          if c not in pinned and (v < 0.4 or 2.9 < v < 3.7)}
    dt = time.monotonic() - t0

    gnd = [c for c, d in dz.items() if base[c] < 0.4 and d < 150]
    rail33 = [c for c, d in dz.items() if base[c] > 2.9 and d < 150]
    pull = [c for c, d in dz.items() if base[c] > 2.9 and 150 <= d <= 820]
    five = [c for c, v in base.items() if v >= 4.0]
    print(f"测了 48 个 Clip、3 轮引脚识别、{len(dz)} 次阻抗探测，用时 {dt:.1f} s")
    print(f"  地：{' '.join(gnd) or '-'}   3.3V 轨/被驱动脚：{' '.join(rail33) or '-'}   "
          f"5V：{' '.join(five) or '-'}   上拉（OLED 的 SCL/SDA）：{' '.join(pull) or '-'}")

    ok, led, res = True, {}, {}
    print("逐项：")
    for pin, name, col in (("pb6", "PB6", "yellow"), ("pb7", "PB7", "green"), ("pa0", "PA0", "purple")):
        hits = found[pin]
        if len(hits) == 1:
            res[pin] = hits[0]
            led[hits[0]] = col
            print(f"  OK  {name} 在 {hits[0]}（计划 {PLAN[pin]}，{col_shift(hits[0], PLAN[pin])}）")
        else:
            ok = False
            print(f"  错  {name}：" + (f"同时出现在 {hits}" if hits else "没找到（小板没插稳，或没上电）"))
    for role, name, pool, col in (("gnd", "小板 GND + OLED GND", gnd, "blue"),
                                  ("3v3", "小板 3V3 + OLED VCC", rail33, "red")):
        c = PLAN[role]
        if c in pool:
            led[c] = col
            print(f"  OK  {name} 在 {c}")
        else:
            ok = False
            led[c] = "red"
            print(f"  错  {name}：{c} 读 {base[c]:.2f} V、不是{'地' if role == 'gnd' else ' 3.3V 轨'}")
    # the 律动小板 has pull-ups of its own on some pins (columns 1..20 under the board):
    # the OLED sits outside the board, so only pull-ups beyond column 20 count
    board_pull = [c for c in pull if int(c[1:]) <= 20]
    pull = [c for c in pull if int(c[1:]) > 20]
    if board_pull:
        print(f"  信息：小板自己的上拉脚在 {board_pull}，不是 OLED")
    planned = [PLAN["oled_scl"], PLAN["oled_sda"]]
    if all(c in pull for c in planned):
        res["oled_scl"], res["oled_sda"] = planned
    elif len(pull) >= 2:
        pull_sorted = sorted(pull, key=lambda c: (c[0], int(c[1:])))
        res["oled_scl"], res["oled_sda"] = pull_sorted[0], pull_sorted[1]
    if len(pull) >= 2:
        for r, col in (("oled_scl", "yellow"), ("oled_sda", "green")):
            led[res[r]] = col
        print(f"  OK  OLED SCL/SDA 上拉在 {res['oled_scl']} / {res['oled_sda']}"
              f"（计划 D21/D22；OLED 已插好并已上电）")
        if len(pull) > 2:
            print(f"  注意：上拉不止两处 {pull}，先用前两个")
    else:
        print(f"  ?   OLED SCL/SDA：只看到上拉 {pull}；按计划 D21/D22 布线，接通后由 I2C 判断")
        res.setdefault("oled_scl", PLAN["oled_scl"])
        res.setdefault("oled_sda", PLAN["oled_sda"])
        led[res["oled_scl"]], led[res["oled_sda"]] = "white", "white"
    if five and PLAN["gnd"] in gnd:
        print(f"  信息：5V 在 {five}（小板 J2-3），只是插在 Clip 里，不接任何网络")
    res["gnd"] = (gnd + [PLAN["gnd"]])[0]
    res["avoid"] = sorted(set(gnd + rail33 + five))
    STATE.write_text(json.dumps(res, indent=1))
    dev.hello()
    paint(dev, led)
    print("灯：亮 = 找到并核对过；红 = 有问题；白 = 待 I2C 确认。" + ("全部通过。" if ok else ""))
    return ok


def load_state() -> dict:
    if not STATE.exists():
        sys.exit("先运行 check")
    return json.loads(STATE.read_text())


def guard(st: dict, clips: list[str]) -> None:
    bad = [c for c in clips if c in st["avoid"] and c != st["gnd"]]
    if bad:
        sys.exit(f"拒绝：{bad} 是电源/被驱动的 Clip，不能放进矩阵网络")


def route() -> None:
    st = load_state()
    nets = {"SCL": [st["pb6"], st["oled_scl"]], "SDA": [st["pb7"], st["oled_sda"]]}
    for clips in nets.values():
        guard(st, clips)
    dev = jf_session()
    dev.ask("SW ALLOPEN")
    dev.hello()
    for name, clips in nets.items():
        print(f"{name}: ", end="")
        jf.do_connect(dev, clips)
    paint(dev, {st["pb6"]: "yellow", st["oled_scl"]: "yellow", st["pb7"]: "green",
                st["oled_sda"]: "green", st["pa0"]: "purple", st["gnd"]: "blue"})
    cct = Cct()
    print(cct.ask("scan", wait=3.0, want="idle").strip())
    out = cct.ask("id off", want="OK") + cct.listen(3.5)
    print(out.strip())
    if "oled ok" in out:
        time.sleep(2)
        print(cct.ask("stat", want="STAT").strip())
        return
    print("没有 ACK：把 SCL/SDA 在矩阵里对调再试一次")
    jf.do_remove(dev, st["pb6"], whole=True)
    jf.do_remove(dev, st["pb7"], whole=True)
    jf.do_connect(dev, [st["pb6"], st["oled_sda"]])
    jf.do_connect(dev, [st["pb7"], st["oled_scl"]])
    out = cct.ask("id off", want="OK") + cct.listen(3.5)
    print(out.strip())
    if "oled ok" in out:
        st["oled_scl"], st["oled_sda"] = st["oled_sda"], st["oled_scl"]
        STATE.write_text(json.dumps(st, indent=1))
        print("对调后成功：OLED 的 SCL/SDA 和预想相反，已在矩阵里换过来，不用重插")
    else:
        print("对调也不行：查 OLED 是否插在 D19–D22、方向是否 GND 在左")


def press(seconds: float) -> None:
    st = load_state()
    guard(st, [st["pa0"]])
    dev = jf_session()
    cct = Cct()
    cct.listen(0.2)
    print(f"按下：{st['pa0']}（PA0）↔ {st['gnd']}（GND）")
    jf.do_connect(dev, [st["pa0"], st["gnd"]])
    out = cct.listen(seconds)
    jf.do_remove(dev, st["pa0"], whole=False)
    out += cct.listen(0.5)
    print("\n".join(l for l in out.splitlines() if l.startswith("EVT")))


def clear() -> None:
    dev = jf_session()
    dev.ask("SW ALLOPEN")
    dev.hello()
    paint(dev, {})
    print("all open, LEDs off")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "guide"
    if step == "guide":
        guide()
    elif step == "check":
        sys.exit(0 if check() else 1)
    elif step == "route":
        route()
    elif step == "press":
        press(float(sys.argv[2]) if len(sys.argv) > 2 else 3.0)
    elif step == "clear":
        clear()
    else:
        print(__doc__)
