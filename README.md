# JumperFree

> 16-Node Agent-Controlled Breadboard Developer Edition

<p>
<a href="https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml"><img src="https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<img src="https://img.shields.io/badge/Python-3.11%20%7C%203.13-3776AB?logo=python&logoColor=white" alt="Python">
</p>

JumperFree 的目标是让 AI 像描述软件连接一样搭建真实电路：Agent 只说明哪些节点属于同一个
电气网络，由确定性路由器决定如何闭合交叉开关，再由板载 MCU 安全地切换开关矩阵。

当前仓库处于 v1.0 开发准备阶段，只做删减、归档与目录整理。软件能力仍未经硬件验证。

## v1.0 目标

v1.0 是 **16 节点、Agent 控制的面包板开发者版本**，最终由三部分组成：

- `agent/`：Agent Skill，把自然语言需求转换为 Netlist，并调用电脑端接口。
- `python/`：电脑端软件，负责 Netlist、自动路由、连接计划与 USB 通信。
- `firmware/`：STM32F103 固件，负责两颗 CH446Q 和 16 颗 RGB LED 的实体控制。

`protocol/` 保存 Python 与 MCU 共用的通信规范与测试向量。

## 当前仓库结构

```text
JumperFree/
├── agent/           # 预留：Agent Skill（尚未实现）
├── python/          # 已实现的主机侧 Python 软件
│   ├── pyproject.toml
│   ├── src/jumperfree/
│   │   ├── core/    # schema、CRC、错误、影子、协议向量解析
│   │   ├── routing/ # 16 节点拓扑、交叉开关路由、短接检查
│   │   ├── board/   # BoardProfile、SwitchPlan、计划编译/校验、LED 映射
│   │   ├── host/    # JF1/JF2 编解码、串口、MockDevice
│   │   └── service/ # 路由状态到 LED 指引的编排
│   └── tests/       # 保留的单元测试
├── firmware/        # 预留：STM32F103 固件（尚未实现）
├── protocol/        # JF2 规范与 JF1/JF2 共享向量
├── docs/            # 现有技术文档
├── .github/         # CI
└── README.md
```

## 当前已完成

Python 主机侧已具备以下软件能力，并可完全在内存或 MockDevice 上运行：

- 16 节点、两颗 CH446Q 风格芯片与 8 条共享 Y 通道的拓扑模型。
- 多端点 Net 的确定性交叉开关路由、资源独占、不同 Net 短接检查、建网与删除。
- BoardProfile、SwitchPlan、先断后通计划编译与校验、LED 节点映射。
- JF1 LED 主机、JF2 事务/租约/故障模型、MockDevice 与串口 Transport。

Agent Skill、STM32F103 固件、真实 CH446Q 控制与硬件验证均**尚未实现**。

## 开发

```bash
cd python
PYTHONPATH=src python -m unittest discover -s tests -t . -v
```

CLI 示例：

```bash
PYTHONPATH=src python -m jumperfree route xbar init --topology v01 --state out/v01.json
PYTHONPATH=src python -m jumperfree route xbar create --net span --nodes Clip0,Clip15 --state out/v01.json
PYTHONPATH=src python -m jumperfree host status --mock
```

更多细节见 [路由文档](docs/routing/overview.md)、[板级计划](docs/arch/board.md) 和
[协议规范](protocol/jf2.md)。
