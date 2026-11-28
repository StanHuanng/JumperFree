# JumperFree mini OLED 演示：律动小板 STM32C532CCT6（CubeMX2 / HAL2）

JumperFree mini 的无跳线用户流程：律动小板（2×20，600 mil）像 DIP 一样跨在中间槽上，0.96" SSD1306 OLED（GND VCC SCL SDA）平插在前排最外侧的孔里。JumperFree 负责检测插接位置、经矩阵连接 I2C、"按下" PA0。主机端是 [`../mini-f103/host/oled_demo.py`](../mini-f103/host/oled_demo.py)。

## 固件功能

- 软件 I2C：PB6 = SCL，PB7 = SDA，开漏 + 上拉，约 100 kHz（线路经过 CH446Q 矩阵）。地址 0x3C / 0x3D，每 1 s 重试一次。
- SSD1306 画面：会眨眼、左右看的机器人眼睛。PA0（上拉）被拉低时，眼睛变开心，屏幕显示点按次数。
- USART2（ST-Link 虚拟串口，115200 8N1）控制台，一行一个命令：

| 命令 | 回复 |
|---|---|
| `id none\|pb6\|pb7\|pa0` | 接线检测：把一个引脚拉低，释放其他引脚，回复 `OK id <x>` |
| `id off` | 回到演示 |
| `scan` | `SCAN <地址>`，以及 SCL/SDA 空闲电平 |
| `stat` | `STAT fw= mode= oled= addr= pa0= taps= frames= dwt=` |

主动事件：`EVT oled ok addr=3C`、`EVT oled no ACK`、`EVT oled lost`、`EVT pa0 low (pressed by matrix)` / `EVT pa0 high`。

## 构建与烧录

ST 包（`stm32c5xx_drivers/`、`stm32c5xx_dfp/`、`arch/`）没有入库，恢复方法与 [`../ch446q-testboard-c5`](../ch446q-testboard-c5/README.md) 相同（ioc2 为 `JF2_LINK.ioc2`）。

```sh
cube cmake --preset debug_GCC_STM32C532CCT6
cube cmake --build --preset debug_GCC_STM32C532CCT6
STM32_Programmer_CLI -c port=SWD freq=1000 mode=UR -w build/debug_GCC_STM32C532CCT6/JF2_LINK.elf -v -rst
```

CMake 工程名沿用 `JF2_LINK`。`CMakeLists.txt` 去掉了 `--specs=nano.specs`。

## 演示流程（主机）

```sh
python host/oled_demo.py guide    # 点亮引导灯：什么颜色插什么
python host/oled_demo.py check    # 先查 JF 矩阵自身，再由小板逐根拉低 PB6/PB7/PA0，JF 测 48 个 Clip，并做阻抗探测
python host/oled_demo.py route    # 按实测位置经矩阵连接 SCL/SDA，启动 OLED
python host/oled_demo.py press    # JF 把 PA0 和 GND Clip 接通约 1 s
python host/oled_demo.py clear
```

串口用环境变量 `JF_PORT`（默认 COM4）和 `CCT_PORT`（默认 COM3）指定。

规划布局：第 21−k 列放 J1/J2 的第 k 脚。实测位置：

| 引脚 | Clip |
|---|---|
| PB6 | D14 |
| PB7 | D15 |
| PA0 | U16 |
| GND | D19（还有 U1、U2） |
| 3V3 | D20（还有 U3） |
| OLED SCL / SDA | D21 / D22 |

供电只经弹簧夹：OLED 的 VCC/GND 和小板的 3V3/GND 插在同一个 Clip 里。矩阵只走 SCL、SDA、PA0。

## 注意

- 上电：先给 JumperFree 上电，再插零件，最后接小板的 USB。断电顺序相反。
- `check` 开头会读 +5V，并测一次 U307 服务通路（不经过面包板）。任一项不正常时直接报 JF 故障，不判断接线。实测中出现过一次全部开关失效、+5V 掉到 4.81 V 的情况；断电后空板重新上电，自检恢复通过。
- 律动小板在 D10 / D12 上有自己的上拉，所以 `check` 只把第 20 列以外的上拉当作 OLED。
