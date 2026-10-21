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

本仓库是这条路线的**第一步：纯软件路由器与仿真**。目前**不包含** CH446Q 驱动、PCB 或固件，也不声称任何硬件已验证。硬件原型实践请见前作 [JumperFree-Board](https://github.com/StanHuanng/JumperFree-Board)。

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

**当前局限（如实说明）**：stage 1 使用**全连接**仿真图——任意两节点之间都有直连 link，所以还没有多跳路径、中继节点或真实开关矩阵的阻塞问题，也**没有重排（rearrange）**。「任意两点可按需连通」不等于「所有多组连接都能无限制同时成立」，后者需要具体开关拓扑、容量与电气约束的证明或测试。

---

### 📈 路线图 (Roadmap)

| 阶段        | 目标                                                                                       | 状态     |
| :---------- | :----------------------------------------------------------------------------------------- | :------- |
| **Stage 1** | **软件路由**：N 节点模型、net 增删查、隔离、原子创建、结构化错误                            | ✅ 已完成 |
| **Stage 1.5** | **确认对标规模**：核实 Jumperless 节点清单与计数口径（物理插孔 ≠ 独立电气节点）            | 📅 计划中 |
| **Stage 1.6** | **交叉点资源图**：建模 CH446Q X/Y 通道、可控交叉点与芯片间链路；多跳 Steiner 近似（基于最短路） | 📅 计划中 |
| **Stage 2** | **有界局部重排**：冲突检测 + 局部换路，以改动连接数 `C_move` 为主代价，超限明确返回“未找到” | 📅 计划中 |
| **Stage 2.5** | **评估**：小规模拓扑穷举参照，统计成功率、耗时、重排次数；错误短接必须为 0                  | 📅 计划中 |
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

# 测试与 100% 行覆盖率（标准库 trace）
python3 -m unittest discover -s tests -t .
python3 scripts/coverage_report.py
```

状态默认保存在 `out/routing-state.json`，可用 `--state PATH` 指定。

---

### 🧩 仓库结构

```text
jumperfree/            包入口（python3 -m jumperfree）
  routing/model.py     路由模型：状态、建网、删网、查询、错误码
  routing/cli.py       route 子命令
docs/routing/          概览 / 模型 / CLI 文档
examples/routing/      状态文件示例
tests/                 标准库 unittest
scripts/coverage_report.py  行覆盖率报告（要求 100%）
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
