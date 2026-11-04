# CH446Q 测试板 + STM32F103C8（寄存器级 CMSIS）

电脑 → USART2 → JF2 核心 → CH446Q 实时切换，5x5 WS2812B 灯板显示当前连接。不依赖 HAL / CubeMX。

## 硬件连接

| 功能 | 引脚 | 说明 |
|---|---|---|
| 串口 | PA2 TX / PA3 RX，115200 8N1 | 测试板上未引出，从核心板排针接。ST-Link V2-1 的虚拟串口或 3.3 V USB-TTL 均可；PA9/PA10 属于 CH446Q |
| CH446Q | 见 [../README.md](../README.md) | 串行模式，H4 跳到 GND |
| WS2812B | PB10 → 灯板 DIN | 灯板 5V/GND 接测试板 +5V/GND；测试板上 PB9 是 AX1，不能用 |
| 供电 | 测试板 +5V | CH446Q 需要 +5V，不能只给 3V3 |
| 烧录 | SWD（核心板自带排针） | 烧完可拔 |

## 构建与烧录

需要 arm-none-eabi-gcc、CMake ≥ 3.22、Ninja（STM32Cube 的 bundle 即可）。

```sh
cmake -S . -B build -G Ninja      # 默认 MinSizeRel
cmake --build build               # Flash ~45 KB / 64 KB，RAM ~14 KB / 20 KB
STM32_Programmer_CLI -c port=SWD freq=1000 mode=UR -e all -w build/CH446Q_LINK_F103.elf -v -rst
```

## 实现要点

- 时钟：HSI 8 MHz → PLL 64 MHz（不依赖外部晶振），APB1 32 MHz。
- 串口接收：DMA1 通道 6 循环搬到 512 B 环形缓冲，主循环取出交给 `jf2_feed`；屏蔽中断期间也不丢字节。
- 用完整 newlib（非 nano）：JF2 核心用 `PRIu64` 打印，nano 的 printf 不支持 `%llu`；栈 4 KB。
- CH446Q 驱动 `app/Src/ch446q.c`：串行地址模式，每个阶段约 0.4 µs。`ch446q_diag` 在发波形的同时从 IDR 回读解码，统计不一致并保存快照，可用 SWD 读出。
- WS2812B `app/Src/ws2812.c`：发码函数放在 RAM（`.RamFunc`），高电平用固定 NOP 产生。在 Flash 里轮询 `DWT->CYCCNT` 会超出约 30 个周期（0 码高电平 0.83 µs，灯全白）。每字节屏蔽中断，实测 T0H≈22、T1H≈45 周期（`ws2812_meas`）。
- 显示 `app/Src/display.c`：一次显示一个字符，轮流播放每个闭合开关 `X n Y m`，同一网络（JF2 tag）同色；新增开关先白闪再优先播放，断开的开关以红色 `-` 加 X/Y 播一次，一次超过 4 个变化时只给汇总提示；无连接时中心呼吸点（绿=ARMED，红=SAFE）；开机显示 `1 2 3` 检查灯板方向。灯链从左下角开始（`FLIP_ROWS`）。

## 已知问题

- 灯板偶有个别灯珠颜色残留/乱色：3.3 V 数据驱动 5 V 灯珠的电平裕量不足。按像素/整帧屏蔽中断、定时重发都试过，效果更差，已回退。硬件解决：灯板电源串二极管降到约 4.5 V，或加 74AHCT1G125 电平转换。
- PC13 LED 按低电平点亮写，未核实核心板极性。
