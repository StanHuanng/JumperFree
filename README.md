# JumperFree

> **让 AI 像调试软件一样搭建真实电路：无跳线原型板的软件路由器**
>
> 团队：高电位队 (High Voltage Team)

[![CI](https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml/badge.svg)](https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml)
[![Stage](https://img.shields.io/badge/Stage-1%20Software%20Router-blue.svg)](#-路线图-roadmap)
[![Python](https://img.shields.io/badge/Python-3.11%20%7C%203.13-brightgreen.svg)](https://www.python.org)
[![Deps](https://img.shields.io/badge/deps-stdlib%20only-lightgrey.svg)](pyproject.toml)

---

### 📖 项目初衷

`JumperFree` 是一块由 AI 驱动的无跳线原型板的设想：Agent 只告诉板子「要连什么」（哪些节点属于同一个电气网络），由确定性路由器决定「怎么连」，最后再由板载 MCU 安全地切换模拟开关矩阵。目标是让小型项目原型可以少接杜邦线、少打早期 PCB。

本仓库提供**软件路由器与仿真**，以及 **Stage-1 Clip LED Host**：通过 JF1 USB-UART 协议控制外接 F103 的 Clip0–15 RGB LED，支持无硬件 Mock。使用方式见 [Host 中文说明](docs/host/cli.md) 与 [JF1 协议](docs/host/protocol.md)；引脚与 BluePill 型号均为 TBD。目前**不包含** CH446Q 驱动、PCB 或固件，也不声称任何硬件已验证。硬件原型实践请见前作 [JumperFree-Board](https://github.com/StanHuanng/JumperFree-Board)。

---

### 🛠️ 核心进展

#### **Stage 1: 空闲资源上的软件路由 [已达成]**

*   **可配置 N 节点模型**：节点数是参数，不预填对标 Jumperless 的“官方”数字（真实节点清单与计数口径待确认）。
*   **网络（net）管理**：支持两点连通或多节点 net 的新建 / 删除 / 查询 / 列表。
*   **隔离保证**：节点与 link 在不同 net 间独占，不会出现非预期短接。
*   **原子创建**：失败的请求不修改状态；成功才写回状态文件。
*   **结构化失败原因**：`NODE_OUT_OF_RANGE`、`NET_TOO_SMALL`、`NODES_ALREADY_IN_OTHER_NET`、`NET_ALREADY_EXISTS`、`NET_NOT_FOUND`、`NO_FREE_PATH`（details 标 `RESOURCE_EXHAUSTED`）。它们只描述仿真状态，不代表“硬件不可能”。
*   **确定性输出**：每条命令输出一行人类摘要 + 一行键排序的 `ROUTE_JSON {...}`。

---

### 🧠 算法

1. **稳定的边排序**：对 net 成员之间的空闲候选 link slot，以 `seed` 做 SHA-256 稳定排序——相同状态、seed 与请求永远得到相同结果。
2. **Kruskal + 并查集**：按上述顺序选边，用 union-find 求出连通全部成员的 spanning tree；凑不齐则返回 `NO_FREE_PATH`。
3. **占用记账**：选中的 link slot 与成员节点登记到该 net；`delete` 一次性释放。

**交叉点资源图（Stage 1.5）**：新增参数化的 X/Y 线资源图，把节点接线和芯片间链路建模为固定边，把芯片内交叉点建模为可控边；芯片线由单一 net 独占，并可检查闭合交叉点是否造成跨 net 短接。它是通用逻辑模型，不包含 CH446Q 寄存器或 SPI 驱动。

**多跳 Steiner 路由（Stage 1.6）**：从编号最小的成员开始，反复以确定性的 0/1 代价最短路（固定接线代价 0、每个新闭合交叉点代价 1）把最近的未连接成员接到当前树上。跨芯片固定链路两端和同线节点按真实电气连接一起占用；每次成功后再运行短接检查。

**有界局部重排（Stage 2，部分完成）**：`route xbar create --rearrange` 会先尝试普通建网；遇到 `NO_FREE_PATH` 后，以廉价 blocking probe 排序候选，并试移除可配置的至多 K 个既有 net（`--max-rip-nets`，默认 3），先路由新 net，再用 Steiner 近似重路由候选 net。完整首轮搜索失败且状态预算尚有余量时，会按原遇见顺序逐个重试已成功路由新 net 和第一个既有 net 的双 net 组合，并以反向字典序重路由既有 net；每次回退额外占用一个状态，不改变候选排名、组合顺序或任何首轮成功结果。搜索以既有 net 的 line 与 crosspoint 集合对称差之和作为 `C_move`，受 `--max-moves` 与 `--max-states` 限制，并在 `ROUTE_JSON.rearrange` 报告统计；它仍不是全局最优算法，选择性回退仍可能漏掉可行顺序。

**评估工具（Stage 2.5，部分完成）**：`scripts/run_eval.py` 在 1–3 颗芯片、2–4 条 X 线、2–3 条 Y 线的 18 个小拓扑上生成确定性的随机增删 net 工作负载，对比纯 Steiner 与有界重排，统计接受/拒绝数量、成功率、移动/搜索状态、预算遗漏、参照一致率以及短接/线独占违规。参照会穷举移除至多两个既有 net 的组合及重路由顺序；它只在相同局部重排邻域内穷举，仍使用 Steiner 建路，不是全局最优参照。运行与指标说明见[小拓扑对比评估](docs/routing/eval.md)。

**V5 对标规模软件评估（Stage 2.5，部分完成）**：`v5-scale` 从随仓库保存的参考资料读取 12 颗芯片的尺寸与角色，构造 288 条独占线、1,536 个交叉点和 64 个外部端点，重复执行相同的确定性工作负载来比较普通 Steiner 与有界重排。默认固定前缀包含零状态预算和 6-move 不足预算探针；拥塞恢复案例需要 7 moves、探索 1 个状态。报告分别统计建网成功、建网失败和删除不存在 net，不把它们混为一个成功率。面包板行映射、`SF_I`–`SF_L` 端点和双通道链路都标记为 **`verified:false`**；这只是在假设接线上的软件规模证据，不代表 V5 硬件容量或硬件验证。详见 [`docs/routing/scale-eval.md`](docs/routing/scale-eval.md)。

**MCU 夹持区与真实场景（Block 1）**：可选的两排 MCU clip zone 把 MCU pin 映射为独立逻辑节点，并提供 ESP32/Blue Pill 外设组合的可执行场景。设计、schema、来源与实测结果见 [`docs/routing/scenarios.md`](docs/routing/scenarios.md)。两排夹点和适配器次序均为 **`verified:false`** 的设计假设，不代表真实硬件已经确认。

**JumperFree V0.1 软件拓扑**：`route xbar init --topology v01` 提供两颗 16×8 CH446Q 风格芯片、Clip0–15 和 8 条同编号共享 Y 通道的资源图；X8–X15 无外接线。模型及全部硬件假设均为 **`verified:false`**，接线与使用示例见 [V0.1 中文说明](docs/routing/v01-topology.md)。

**当前局限（如实说明）**：stage 1 的旧模型仍是全连接仿真图；xbar 的重排只是少量 net 上的有界搜索，重路由仍采用贪心 Steiner 近似，不是全局最优算法，也不提供 Clos 网络的可重排保证。除非完整资源图的连通性等廉价不变量能够证明不可达，预算内失败只报告“未找到”，不声称硬件拓扑绝对不可路由。

---

### 📈 路线图 (Roadmap)

| 阶段        | 目标                                                                                       | 状态     |
| :---------- | :----------------------------------------------------------------------------------------- | :------- |
| **Stage 1** | **软件路由**：N 节点模型、net 增删查、隔离、原子创建、结构化错误                            | ✅ 已完成 |
| **Stage 1.5** | **确认对标规模**：已记录 Jumperless 节点计数口径（物理插孔 ≠ 独立电气节点；参考资料，未硬件验证） | ✅ 已完成 |
| **Stage 1.6** | **真实拓扑路由**：在交叉点资源图上实现确定性多跳 Steiner 近似（基于最短路） | ✅ 已完成 |
| **Block 1** | **MCU 场景层**：两排逻辑夹持区、确定性器件放置、9 个来源可追溯的路由场景（硬件假设未验证） | ✅ 软件完成 |
| **Stage 2** | **有界局部重排**：blocking probe 排序，默认至多重路由三个既有 net，以连接集合差 `C_move` 限制改动；尚非全局最优 | 🚧 部分完成 |
| **Stage 2.5** | **评估**：小规模参照加 V5 对标资源规模的确定性安全/时限评估；假设接线均未硬件验证，不声称全局最优 | 🚧 部分完成 |
| **Stage 3** | **硬件映射**：映射到 CH446Q 开关状态，由本地 MCU 做供电/电流安全检查并可回退               | 📅 计划中 |

> 三级 Clos 的重排上界等理论结论只作方法参考；在硬件拓扑被证明满足条件前，不套用其数值。

---

### 🚀 快速开始

仅需 Python ≥ 3.11，无第三方依赖。在仓库根目录：

```bash
python3 -m jumperfree route init --nodes 8 --seed 7
python3 -m jumperfree route create --net NET_1 --nodes 0,1,2
python3 -m jumperfree route create --net NET_2 --nodes 3,4
python3 -m jumperfree route query
python3 -m jumperfree route delete --net NET_1
python3 -m jumperfree route list

# Stage-1 LED Host（内存模拟，无串口依赖）
python3 -m jumperfree host set --clip 0 --rgb 255,128,0 --mock
python3 -m jumperfree host status --mock

# 多跳交叉点资源图（独立状态文件 out/crossbar-state.json）
python3 -m jumperfree route xbar init --topology small --chips 3 --x-lines 4 --y-lines 4
python3 -m jumperfree route xbar create --net LONG --nodes N0,N4,N11
python3 -m jumperfree route xbar create --net EXTRA --nodes N1,N5 --rearrange --max-moves 8 --max-states 2000 --max-rip-nets 3
python3 -m jumperfree route xbar query --net LONG
python3 -m jumperfree route xbar delete --net LONG

# 测试与 100% 行覆盖率（标准库 trace）
python3 -m unittest discover -s tests -t .
python3 scripts/coverage_report.py

# 确定性小拓扑评估（JSON 含参照一致率；短接计数必须为 0）
python3 scripts/run_eval.py --seed 7 --trials 20 --out out/eval.json

# V5 对标资源规模评估（假设接线；报告与计时分离）
python3 scripts/run_scale_eval.py --seed 7 --trials 60 --out out/scale-eval.json --max-seconds 30
```

`small_topology` 会在每条 X 线上挂接一个节点，因此跨芯片路径可能需要把中继芯片上的节点也加入 net。

状态默认保存在 `out/routing-state.json`，可用 `--state PATH` 指定。

---

### 🧩 仓库结构

```text
jumperfree/            包入口（python3 -m jumperfree）
  host/               Stage-1 Clip LED Host：JF1 协议、Mock/可选串口与 CLI
  routing/model.py     路由模型：状态、建网、删网、查询、错误码
  routing/crossbar.py  参数化交叉点资源图、拓扑验证与短接检查
  routing/steiner.py   确定性多跳 Steiner 近似与 xbar 状态
  routing/rearrange.py Stage 2 有界局部重排与 C_move 统计
  routing/eval.py      Stage 2.5 确定性评估与局部穷举参照
  routing/v5_scale.py  未验证接线的 V5 对标资源规模拓扑
  routing/v01_topology.py  未验证接线的 JumperFree V0.1 双芯片拓扑
  routing/scale_eval.py 规模工作负载、安全断言与双路由回放
  routing/scenarios.py 场景 schema、确定性放置与 MCU-zone 适配器
  routing/cli.py       route 子命令
docs/routing/          概览 / 模型 / CLI 文档
docs/host/             Host 使用说明 / JF1 协议（中文；硬件未验证）
examples/routing/      状态文件与 MCU/外设路由场景
tests/                 标准库 unittest
scripts/coverage_report.py  行覆盖率报告（要求 100%）
scripts/run_eval.py         小拓扑评估 JSON 生成器
scripts/run_scale_eval.py   对标规模评估 JSON 与独立计时生成器
.github/workflows/ci.yml    Python 3.11 + 3.13 CI
```

---

### 🤝 关于我们

**高电位队**
专注于单片机与 FPGA 电子开发与原型设计。团队成员涵盖硬件、嵌入式软件、PCB 设计等领域，力求推动实验平台的发展与普及。

*   **前作**：[JumperFree-Board](https://github.com/StanHuanng/JumperFree-Board)（STM32F103 + CH446Q + WS2812 原型）。
*   **致谢**：感谢 [JumperlessV5](https://github.com/Architeuthis-Flux/JumperlessV5) 提供的卓越灵感。

---
> **文档指引**：从 [`docs/routing/overview.md`](docs/routing/overview.md) 开始阅读。
