# JumperFree


<p>
<img src="https://img.shields.io/badge/version-1.0-34495E" alt="version 1.0">
<img src="https://img.shields.io/badge/License-Proprietary-d97757" alt="License: Proprietary">
</p>

>让 Agent 像操作和调试软件一样，辅助用户搭建真实电子电路。Agent 负责理解需求，确定性 Router 负责计算连接，STM32 控制器负责安全执行，实体硬件负责真实导通。
> 

## 为什么做 JumperFree

嵌入式开发完成代码后，往往还要手动查引脚、连接开发板和模块、核对跳线、烧录与排错。即使只是运行一个简单 Demo，也有不少重复、容易出错的接线工作。

JumperFree 试图缩短**从软件工程到实体电路**的距离：用户主要描述需要什么连接，并把器件放到指定位置；软件负责规划和路由，板上控制器负责执行。

## 当前仓库结构

```text
JumperFree/
├── agent/           # Agent Skill
├── python/          # 主机侧 Python 软件
├── firmware/        # 软件
├── protocol/        # 规范
├── docs/            # 技术文档
├── .github/         # CI
└── README.md
```
 **致谢**：感谢 [JumperlessV5](https://github.com/Architeuthis-Flux/JumperlessV5) 提供的卓越灵感。
