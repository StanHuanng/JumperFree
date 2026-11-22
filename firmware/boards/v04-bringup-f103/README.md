# JumperFree v0.4 板载固件（STM32F103C8T6，寄存器级）

v0.4 主板（48 Clip，前后两排各 24 列；7 片 CH446Q；48 颗 WS2812B）的 bring-up 固件和主机工具。电脑经板载 CH340（USART1 PA9/PA10，115200 8N1）连到 F103。

## 目录

| 路径 | 内容 |
|---|---|
| `firmware/` | CMake + arm-none-eabi 工程，输出 `build/V04_BRINGUP.elf` |
| `firmware/app/jf2/` | JF2 核心的 v0.4 加宽副本（见下文"与 `firmware/src` 的关系"） |
| `firmware/flash_and_read.py` | 经 ROM USART bootloader 烧录并打印控制台 |
| `host/v04_jf2.py` | JF2 实时建网/删网工具，`c` / `d` / `x` / `l` / `clr` |
| `host/oled_demo.py` | 无跳线用户流程演示（配合 [`../v04-oled-demo-c5`](../v04-oled-demo-c5/README.md)） |

## 构建与烧录

```sh
cd firmware
cmake -S . -B build -G Ninja
cmake --build build
python flash_and_read.py COM4     # 运行中的固件收到 boot 命令后进入 bootloader；空片：按住 SW3，点 SW2，松开 SW3
```

## 控制台（行命令，无前缀）

`help` 列出全部命令。常用：

| 命令 | 作用 |
|---|---|
| `info` / `adc` | 时钟、复位原因、E-stop；+5V / −7V / 3V3A / VER_ID 监测 |
| `selftest [svc\|bus\|stb\|rst\|switch\|short]` | 矩阵自检，面包板必须为空 |
| `conn <clip> <clip>` / `nets` / `clr` | 控制台建网、列出、全断 |
| `find` | 找出被导线或元件连在一起的 Clip，并点亮它们的 LED（面包板上不能有通电的电路） |
| `meas <clip>` | 测 Clip 电压（经 BUS 和 MEAS_A） |
| `sw <chip1-7> <x> <y> <0\|1>` / `probe` | 单开关写入；TEST_SRC 低→高阶跃，读 PA0/PA5 |
| `led ...` | LED 测试 |

以 `J` 开头的行交给 JF2（不回显）。控制台的矩阵命令会结束 JF2 会话（所有开关断开，主机要重新 HELLO）；控制台用过矩阵后，第一个 JF2 帧会清掉控制台建的连接。

## JF2 拓扑（board=v04）

- 一个网络 = 一条 BUS（CH446Q 的 X，所有芯片共用，0..15）+ 一个 tag（1..15）。
- Clip FLIPn 在芯片 n/8（U301..U306）的 Y(n%8)，操作写作 `+chip:bus:y@tag`。
- chip 6 = U307，只开放 y6 = TEST_SRC（PB8 经 1.5 kΩ，约 3.3 V，信号级）和 y7 = SVC_GND。主机名为 `3V3` / `GND`，主机拒绝把两者放进同一网络。

## 与 `firmware/src` 的关系

`firmware/app/jf2/` 是 `firmware/src` + `firmware/include` 的副本，按 v0.4 拓扑加宽：7 片芯片、X 0..15、48 个 LED、影子 480 B，SW STATUS / TAGS 按芯片数输出，操作 token 解析改为通用十进制。仓库核心目前仍是 v01（2 片、8×8）。把这些改动参数化合回 `firmware/src`、并更新 `protocol/` 和 `python/` 主机的工作还没做。

## 硬件约束（固件已遵守）

- CH446Q VDD = +5 V、VEE = −7 V。+5V / −7V 不在范围内时，矩阵保持复位。
- CH_RST（PA8 开漏）默认被上拉 = 全断。电源正常、LED 清空后才释放。
- 单开关 ≤ 15 mA。Clip 到 Clip 约 100 Ω。矩阵只走信号，不给负载供电。
- 上电顺序：先给 JumperFree 上电，再给用户电路上电；断电顺序相反。
