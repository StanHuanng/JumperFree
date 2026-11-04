# CH446Q 测试板 + 律动小板 STM32C532CCT6（CubeMX2 / HAL2）

电脑 → ST-Link 虚拟串口（USART2 PA2/PA3，115200）→ JF2 核心 → CH446Q 实时切换。律动小板直接插在测试板 U3 上，串口经板载调试接口 J3 到 ST-Link，无需另接线。

## 工程来源与恢复 ST 包

工程由 `cube mx project create-from-mcu`（STM32C532CCT6、DFP 2.1.0）创建，`CH446Q_LINK.ioc2` 中配置了 USART2（IT + 回调注册）、PE2 和 10 个 CH446Q 输出脚（初始低电平）。CubeMX2 导出时复制进来的 ST 包（`stm32c5xx_drivers/`、`stm32c5xx_dfp/`、`arch/`，约 28 MB）没有入库，构建前先恢复：

```sh
cube mx ide-project generate -p CH446Q_LINK.ioc2 -s CH446Q_LINK \
  --build-target "CH446Q_LINK.debug_GCC+STM32C532CCT6" \
  --destination ../export-stage --format CMake --source include-packs-from-local
# 只把 export-stage 里的 stm32c5xx_drivers/ stm32c5xx_dfp/ arch/ 复制到本目录
```

不要用 `--force` 直接生成到本目录：会把 `main.c`、`CMakeLists.txt` 重置成模板。

## 构建与烧录

```sh
cube cmake --preset debug_GCC_STM32C532CCT6
cube cmake --build --preset debug_GCC_STM32C532CCT6
STM32_Programmer_CLI -c port=SWD freq=1000 mode=UR -e all -w build/debug_GCC_STM32C532CCT6/CH446Q_LINK.elf -v -rst
```

链接脚本已把栈改为 8 KB、堆 4 KB，`CMakeLists.txt` 去掉了 `--specs=nano.specs`（JF2 核心需要 `%llu`）。4 MHz SWD 烧录出现过一次校验失败，用 `freq=1000` 稳定。

## 已验证

- 离板（未接 CH446Q）：`host/ch446q_check.py` 连续 3 次 PASS，每步约 115 ms；`ch446q_diag` 从引脚回读解码的矩阵每一步都与 JF2 一致，读回 0 错误，AX/AY 全程为低。
- 上板：通过 `host/jf2_raw.py` 闭合/断开 X0-Y0、X0-Y1、X0-Y7，万用表确认通断符合。

## 注意

- PA11/PA12 是律动小板的 USB D−/D+，在测试板上接 CH446Q 的 AX0/AX3，所以这块板不能同时用律动小板的 USB。
- 用 SWD 热插拔读内存后要复位一次，否则 USART2 中断不再触发。
