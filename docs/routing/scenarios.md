# MCU 夹持区与路由场景

本目录描述 Block 1 的逻辑场景格式，以及 `examples/routing/scenarios/` 中可重复执行的样例。场景先完成完整校验和确定性放置，再生成拓扑与路由请求；加载过程不会接触或修改路由状态。

## 硬件假设

MCU 区目前是逻辑仿真模型：面包板之外有两排独立夹点，每个夹点是独立的 crossbar X 节点，MCU 板不占面包板行。该假设的状态是 **`verified:false`**。夹点次序、排针方向、电压、电流能力和真实开关芯片实现均未经过硬件验证，不能把仿真连通性当作电气安全证明。

启用 MCU 区后，逻辑芯片 `MCU0` 的节点名为 `MCU_R{row}_C{col}`。它通过固定数量的 Y trunk 连接到 `C0`；夹点多不等于可同时跨区的 net 多。默认 `mcu_links=1`，因此最多只有一个独立 MCU↔面包板 net 能跨越该 cut。

## Schema version 1

根对象必须包含：

- `id`、`title`；
- `mcu`：`kind` 和非空 `pins`，位置写作 `R0C3` 或 `{"row":0,"col":3}`；
- `modules`：每项包含唯一 `id`、`kind`、`placement` 和非空 `pins`；
- `nets`：每项包含唯一 `id` 和至少两个不同的 `module.pin` 成员；
- 非空 HTTP(S) `sources`、`assumptions` 列表和 `expected`。

可选 `schema_version` 目前只能为 `1`。可选 `topology` 可设置 `chips`、`x_lines`、`y_lines`、`links_per_pair`、`clip_rows`、`clips_per_row`、`mcu_links`；`clip_rows` 必须为 2。

模块 `placement` 必须且只能使用一个选择器：闭区间 `row_range:[lo,hi]`，或离散且不重复的 `rows:[...]`。pin 位置可为绝对行号、相对位置 `{"offset":n}`，或 `null` 自动放置。算法先按模块 ID/pin 名预留所有绝对行，再按模块 ID 选择最小可用 anchor，最后按 pin 名把 `null` 分配到最低空闲行。它不根据路由结果回溯或移动器件。

同一供电域使用一个多成员 net；3.3V 与 5V 始终分开。一个端点只能属于一个 net。`expected.routeable=false` 时还必须给出 `error_code` 和非空 `known_limitation`。

Python API：

```python
from jumperfree.routing import (
    load_scenario,
    scenario_to_route_requests,
    scenario_to_topology,
)

scenario = load_scenario("examples/routing/scenarios/stm32-relay.json")
topology = scenario_to_topology(scenario)
requests = scenario_to_route_requests(scenario)
```

## 固定测试 Profile P 与实测结果

所有正向库场景固定使用：

```json
{"chips":2,"x_lines":16,"y_lines":24,"links_per_pair":8,"clip_rows":2,"clips_per_row":20,"mcu_links":16}
```

下表结果来自 2026-10-21 的完整 `unittest` 场景集；每个场景均从新状态重复路由，并通过独立连通遍历和短路检查。

| 场景 | 模块（电源连接器也计入） | net 数 | Profile P 结果 |
|---|---|---:|---|
| ESP32 + SSD1306 + SG90 | OLED、servo、dual-rail power（3） | 6 | 通过 |
| Blue Pill + SSD1306 + DHT11 | OLED、DHT11、3.3V power（3） | 5 | 通过 |
| Blue Pill + SSD1306 + MPU6050 | OLED、MPU6050（2） | 4 | 通过 |
| Blue Pill + HC-SR04 + buzzer driver | HC-SR04、buzzer、5V power（3） | 5 | 通过 |
| Blue Pill + TB6612 channel A + encoder | driver、encoder、logic power（3） | 8 | 通过 |
| Blue Pill + HC-05 | HC-05（1） | 4 | 通过 |
| Blue Pill + nRF24L01+ | radio（1） | 7 | 通过 |
| Blue Pill + LCD1602 I2C backpack | LCD（1） | 4 | 通过 |
| Blue Pill + relay module | relay（1） | 3 | 通过 |

负向 fixture 使用默认容量（一个 MCU trunk）：第一个 MCU↔面包板 net 成功，第二个保持输入状态不变并返回 `NO_FREE_PATH`。这表示固定 cut 容量不足，不是 schema 错误。

每个 JSON 文件内都记录了接口来源和项目级假设。来源支持器件接口；具体组合、GPIO 选择、夹点顺序、适配器和供电方案仍属于显式假设。
