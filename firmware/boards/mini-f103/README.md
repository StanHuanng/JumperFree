# JumperFree mini 板载固件（STM32F103C8T6，寄存器级）

JumperFree mini 主板（48 Clip，前后两排各 24 列；7 片 CH446Q；48 颗 WS2812B）的 bring-up 固件和主机工具。电脑经板载 CH340（USART1 PA9/PA10，115200 8N1）连到 F103。

## 目录

| 路径 | 内容 |
|---|---|
| `firmware/` | CMake + arm-none-eabi 工程，输出 `build/JF_MINI.elf` |
| `firmware/flash_and_read.py` | 经 ROM USART bootloader 烧录并打印控制台 |
| `host/mini_jf2.py` | JF2 实时建网/删网工具，`c` / `d` / `x` / `l` / `clr` |
| `host/oled_demo.py` | 无跳线用户流程演示（配合 [`../mini-oled-demo-c5`](../mini-oled-demo-c5/README.md)） |

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
| `sw <chip1-7> <x> <y> <0\|1>` / `probe [ms]` | 单开关写入；TEST_SRC 低→高→低阶跃，每步稳定 ms（默认 4），读 PA0/PA5 |
| `led ...` | LED 测试 |
| `scope <A> [B\|-] [hz] [n]` | 慢速双通道示波：Clip → BUS → U307 Y4/Y5 → MEAS_A/B → PA0/PA5，TIM3 触发 ADC1 + DMA，一次 1024 点 |
| `la <clip> [f\|r\|a\|i] [wait_ms] [win_us]` | 单通道逻辑边沿捕获：Clip → BUS → U307 Y6 → R409 → PB8（软件轮询，约 0.5 µs） |
| `lac ...` | 同上，TIM4 硬件输入捕获（IC3 上升沿 + IC4 下降沿，15.6 ns） |

以 `J` 开头的行交给 JF2（不回显）。控制台的矩阵命令会结束 JF2 会话（所有开关断开，主机要重新 HELLO）；控制台用过矩阵后，第一个 JF2 帧会清掉控制台建的连接。

## 示波与逻辑捕获

`scope`、`la`、`lac` 不算控制台矩阵命令：JF2 路由保持不变。它们只临时闭合 U307 的 Y4/Y5（示波）或 Y6（逻辑）。Clip 不在任何网络里时，再临时借一条空闲 BUS（从 BUS15 往下找）。抓完全部断开。

- **示波**：前端 Vadc = 0.2545 × Vbus + 0.5758 × VDDA，输入约 −7.4…+5.5 V。单通道最高 500 kS/s，双通道 250 kS/s/通道。模拟带宽约 6–7 kHz（分压电阻 + C401 100 pF）。
- **逻辑**：闭合 Y6 之前，PB8 先改成浮空输入，所以探测不会驱动总线。JF2 的 `3V3` 网络占用 Y6 时拒绝执行。`i` 触发 = 线空闲（高）≥ 50 µs 后的第一个下降沿，用来对准 I2C 传输开头。最多 512 个边沿。
- **`lac`**：PB8 = TIM4_CH3。抓取时 PB9（LED 数据）保持低电平，抓完 `ws2812_hw_setup()` 把 TIM4 还给 LED，LED 颜色不变。边沿对间隔小于约 3 µs 会丢边沿（输出里的 `lost`），≥ 1 MHz 的信号到不了 PB8。

主机工具（`host/`）：

| 文件 | 作用 |
|---|---|
| `jf_scope.py` | 抓 `scope`，换算电压，算频率/占空比/峰峰值，可存 PNG/CSV |
| `lac.py` | 抓 `lac`：`pwm`（频率、占空比、抖动）、`uart`（自动波特率 + 8N1 解码）、`raw` |
| `la_i2c.py [--hw]` | 分两次抓 SCL 和 SDA，对齐后解码 I2C 并量时序。单通道对齐可能错开一个 SCL 高电平，所以取多次抓取中最常见的移位 |
| `scope_tests.py` | 带宽扫频和电源/总线实测脚本（配合产生测试信号的 ESP32-C3） |

## JF2 拓扑（board=mini）

- 一个网络 = 一条 BUS（CH446Q 的 X，所有芯片共用，0..15）+ 一个 tag（1..15）。
- Clip FLIPn 在芯片 n/8（U301..U306）的 Y(n%8)，操作写作 `+chip:bus:y@tag`。
- chip 6 = U307，只开放 y6 = TEST_SRC（PB8 经 1.5 kΩ，约 3.3 V，信号级）和 y7 = SVC_GND。主机名为 `3V3` / `GND`，主机拒绝把两者放进同一网络。

## 与 `firmware/src` 的关系

本工程直接编译仓库的共享 JF2 核心（`firmware/src` + `firmware/include`），用编译定义 `JF2_PROFILE_MINI` 选 mini 板级 profile：7 片芯片、X 0..15、48 个 LED、影子 560 B，SW STATUS / TAGS 按芯片输出，U307 只开放 y6/y7。差异表见 [`protocol/jf2.md`](../../../protocol/jf2.md#板级-profile)，host 测试为 `make -C firmware test-mini`。`python/` 的 JumperFree Host 按 HELLO 的 `board=mini` 选这块板的 geometry。

## 硬件约束（固件已遵守）

- CH446Q VDD = +5 V、VEE = −7 V。+5V / −7V 不在范围内时，矩阵保持复位。
- CH_RST（PA8 开漏）默认被上拉 = 全断。电源正常、LED 清空后才释放。
- 单开关 ≤ 15 mA。Clip 到 Clip 约 100 Ω。矩阵只走信号，不给负载供电。
- 上电顺序：先给 JumperFree 上电，再给用户电路上电；断电顺序相反。
