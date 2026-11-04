# 板级工程

`firmware/src` + `firmware/include` 的 JF2 核心接到真实硬件上的可烧录工程。两个工程都直接编译仓库里的核心源文件，不再各自复制一份。

| 目录 | 主控 | 硬件 | 状态（2026-11-04） |
|---|---|---|---|
| [`ch446q-testboard-f103`](ch446q-testboard-f103/README.md) | LCKFB-DKX STM32F103C8T6 核心板 | CH446Q 测试板 + 5x5 WS2812B 灯板 | 上板实测：JF2 单点开关、Clip 建网/删网，万用表确认导通与隔离；灯板轮播显示 |
| [`ch446q-testboard-c5`](ch446q-testboard-c5/README.md) | 律动小板 STM32C532CCT6 | CH446Q 测试板 | 上板实测：JF2 单点开关；离板用引脚回读验证 Clip 建网/删网 |

## CH446Q 测试板

一片 CH446Q（8x16），主控插在 U3（2x20，排距 600 mil）。CH446Q 用 **串行地址模式**：跳线 H4 的 P/-S 接 GND。

| 信号 | MCU 引脚 | 说明 |
|---|---|---|
| STB / CS-CK / DAT | PA8 / PA9 / PA10 | 串行地址：DAT 上依次送 ADDR6..0（CS/CK 上升沿），再送开关数据并给 STB 高脉冲 |
| AX0 AX1 AX2 AX3 | PA11 PB9 PB8 PA12 | 串行模式下手册要求接 GND，固件保持输出低 |
| AY0 AY1 AY2 | PB7 PB6 PB5 | 同上 |
| +5V / GND | U3 第 18 / 19、39、40 脚 | CH446Q VDD=+5V，VEE=−7V（TPS63700） |

JF2 v01 拓扑是两片芯片各 8 条 X、共用 8 条 Y；测试板只有一片，固件把 JF2 的 chip c、x 映射到 CH446Q 的 X(8c+x)，Y 不变。于是 ClipN 正好在 H2 第 N+1 脚（XN），Y0–Y7 在 H3。

律动小板与 F103 核心板在 U3 上引脚兼容，可以直接互换。

## 主机工具（两个工程的 `host/` 相同用法）

```sh
python host/jf2_raw.py --port COM3                    # SW STATUS，列出闭合的 X-Y
python host/jf2_raw.py --port COM3 +0:5:7@1           # 闭合 X5-Y7（网络 tag 1）
python host/jf2_raw.py --port COM3 -- -0:5:7          # 断开（- 开头的 token 前加 --）
python host/jf2_raw.py --port COM3 --allopen          # 全部断开并重新 HELLO
python host/ch446q_check.py --port COM3 [--no-swd] [--keep]
```

`ch446q_check.py` 走 `ControlService -> DeviceClient -> SerialTransport`（与 MCP 相同路径）做 Clip 建网/删网。默认在结束时用 SWD 读固件里的 `ch446q_diag`（从 GPIO 输入寄存器解码出的开关矩阵）；没接 SWD 时用 `--no-swd` 改为核对设备影子。

注意：`STM32_Programmer_CLI mode=HOTPLUG` 读内存后，目标板的 NVIC 中断使能会被清掉（串口不再收），所以脚本在 SWD 读完后会复位板子。
