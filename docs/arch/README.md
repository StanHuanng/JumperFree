# V0.1 架构概览

本文记录已采纳的架构方向与实施边界；当前只有 Python 软件与内存 Mock，所有电气行为、引脚、时序、亮度及保护能力均**未经硬件验证**。各包的当前职责、依赖门禁与遗留 I/O 边界见 [当前 Python 分层](layers.md)。

## 分层与数据流

| 层 | 职责与依赖方向 |
| --- | --- |
| L4 应用服务 `service/` | 编排请求、事务、状态与策略；CLI/Agent 从这里进入，组合路由和设备调用 |
| L3 路由 `routing/` | 纯、确定性的拓扑寻路、重排与短接检查，只依赖 `core/` |
| L2 板级 `board/` | 将路由结果编译为 SwitchPlan / LedPlan；依赖 routing 类型和 core，不执行设备 I/O |
| L1 设备与传输 `device/` + `host/` | DeviceClient → HostSession → Transport；协议编解码、超时、会话；host 不依赖 board、routing 或 service |
| L0 固件 `fw/` | 可移植 C 核心 → 驱动 → 硬件接口 → port；校验、执行、维护影子，不做路由 |

`core/` 是各层共享的纯格式与错误基础，不是额外业务层，只允许导入标准库和自身。`tests/test_layering.py` 通过 AST 强制当前 Python 包的依赖方向。

主要流程为 **route → plan → device**：意图先经过纯路由，再经板级编译与 plan 校验，服务层完成一致性检查、写前意图记录，最后调用设备事务。设备成功后才落主机状态。JF2、SwitchPlan、DeviceClient、固件与持久化事务属于后续里程碑，M1 不实现它们。

## 协议与安全默认值

JF1 的 `SET/CLR/CLRALL/STATUS` 字节格式、语义及当前错误应答冻结于 [共享向量](../../vectors/protocol/jf1.vec)，格式见 [向量说明](../../vectors/README.md)。未来 JF2 使用独立前缀，与 JF1 同口并存，通过 HELLO / caps 协商；老设备降级为 LED-only，不把 JF1 改成开关协议。

后续开关默认在上电、复位、任何故障、租约过期与 `ALLOPEN` 时全断；提交强制先断后通，中途故障全断。一次性 CLI 默认 `LEASE 0` 保持连接，此时被动 UART 无法检测拔线；持续会话默认租约，心跳中断后租约过期全断。设备重启后不静默恢复，默认严格对账，恢复须用户显式请求。以上为设计约束，尚未实现或经硬件验证。

CH446Q 不可回读，设备读回仅为固件影子，不能证明物理通断。V0.1 不预留 Tester 探针/ADC，不实现连通探测，后续 `SELFTEST 2` 返回 `UNSUPPORTED`；自动化 HIL 不在范围，物理验证靠人工万用表记录。不得宣称已有过流保护。V0.1 采用 net 电源轨声明，由 M3 service 层拒绝不同电源轨的合并并返回 `RAIL_CONFLICT`。

## 里程碑状态

| 阶段 | 产出 | 状态 | 硬件 |
| --- | --- | --- | --- |
| M0 | host guide 基线 | 已完成 | 否 |
| audit / layering | core、board、service 拆分与依赖门禁 | 已完成（PR #18 并入 main） | 否 |
| M1 | JF1 共享向量、解析器、架构文档与 ADR | 本 PR | 否 |
| M2 | BoardProfile、SwitchPlan、compile/verify、route plan | 后续 | 否 |
| M3 | JF2 主机与 MockDevice、DeviceClient、租约/事务；net 电源轨与 RAIL_CONFLICT（service） | 后续 | 否 |
| M4 | 可移植 C 固件核心、驱动与 jf_sim，共享向量 | 后续 | 否 |
| M5 | Python ↔ jf_sim 的 FIL 集成与差分测试 | 后续 | 否 |
| M6 | F103 port、UART、LED 与实测证据 | 后续，未经硬件验证 | 是 |
| M7 | 单片 CH446Q、RST 与逐点人工校准 | 后续，未经硬件验证 | 是 |
| M8 | 双芯片与共享 Y 的人工连通/隔离记录 | 后续，未经硬件验证 | 是 |
| M9 | SAFE、租约、IWDG 与故障全断实测 | 后续，未经硬件验证 | 是 |
| M10 | 原计划的 Tester / 自动化 HIL | 按 D5 排除，不列为交付 | — |
| M11（可选） | MCU 夹持区、host serve 等 | 待定；电源轨声明已提前到 M3 | 视功能 |

M0–M5 只验证软件；M6 起需要硬件证据。`verified` 只能经附证据文件的 PR 变更，工具不得自动翻转。决定的背景与影响见 [ADR 0001](../adr/0001-layering.md) 至 [ADR 0008](../adr/0008-max-ops.md)。
