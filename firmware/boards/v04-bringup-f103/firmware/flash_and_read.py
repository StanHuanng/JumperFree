"""Flash build/V04_BRINGUP.elf over COM4 (ROM USART bootloader) and print the console.
A running bring-up firmware (>= boot command) is asked to enter the bootloader first;
otherwise press: hold SW3, tap SW2, release SW3."""
import subprocess, time, os, sys, serial
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # programmer output is not GBK-safe
PORT = sys.argv[1] if len(sys.argv) > 1 else "COM4"
cli = os.path.expandvars(r"%LOCALAPPDATA%\stm32cube\bundles\programmer\2.23.0\bin\STM32_Programmer_CLI.exe")
elf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "V04_BRINGUP.elf")
try:
    s = serial.Serial(PORT, 115200, timeout=0.2); s.write(b"\rboot\r"); time.sleep(0.3); s.close()
except Exception as e:
    print("boot cmd:", e)
time.sleep(0.3)
deadline = time.time() + 110; ok = False; n = 0; o = ""
while time.time() < deadline:
    n += 1
    r = subprocess.run([cli, "-c", f"port={PORT}", "br=115200", "P=EVEN", "-w", elf, "-v", "-s", "0x08000000"],
                       capture_output=True, text=True, errors="replace")
    o = r.stdout + r.stderr
    if "Chip ID" in o:
        print(f"--- attempt {n}: connected")
        print("\n".join(l for l in o.splitlines() if any(k in l for k in ("Chip ID", "verif", "Verif", "rror", "Start"))))
        ok = "verified successfully" in o
        break
    time.sleep(1.5)
if not ok:
    print("FLASH NOT CONFIRMED after", n, "attempts"); print(o[-1500:]); sys.exit(1)
time.sleep(0.5)
s = serial.Serial(PORT, 115200, timeout=0.2)
buf = b""
for cmd in (b"", b"\r", b"info\r", b"adc\r"):
    s.write(cmd); t = time.time()
    while time.time() - t < 1.5: buf += s.read(512)
s.close()
print("--- serial output ---"); print(buf.decode(errors="replace"))
