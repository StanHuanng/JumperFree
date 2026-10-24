# JumperFree

> **让 AI 像调试软件一样搭建真实电路：无跳线原型板的软件路由器**

<p>
<a href="https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml"><img src="https://github.com/StanHuanng/JumperFree/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<img src="https://img.shields.io/badge/Python-3.11%20%7C%203.13-3776AB?logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/coverage-100%25-2D5A43" alt="Coverage">
<img src="https://img.shields.io/badge/dependencies-stdlib%20only-34495E" alt="Dependencies">
<img src="https://img.shields.io/badge/MCU-STM32F103-03234B?logo=stmicroelectronics&logoColor=white" alt="MCU">
<img src="https://img.shields.io/badge/switch-CH446Q-d97757" alt="Switch">
<img src="https://img.shields.io/github/last-commit/StanHuanng/JumperFree?color=555" alt="Last commit">
<img src="https://img.shields.io/github/languages/code-size/StanHuanng/JumperFree?color=555" alt="Code size">
</p>

---

### 项目初衷

`JumperFree` 是一块由 AI 驱动的无跳线原型板：Agent 只告诉板子「要连什么」（哪些节点属于同一个电气网络），由确定性路由器决定「怎么连」，再由板载 MCU 安全地切换模拟开关矩阵。目标是让小型项目原型少接杜邦线、少打早期 PCB。

---

### 路线图

| 里程碑 | 内容 | 状态 |
| :-- | :-- | :-- |
| **M0** | `host guide` 入主干（#17） | ✅ 已完成 |
| — | core / board / service 分层与依赖方向测试（#18） | ✅ 已完成 |
| **M1** | JF1 协议基线冻结为共享向量 `vectors/protocol/jf1.vec`，架构文档与 ADR（#19） | ✅ 已完成 |
| **M2** | 板级层：`BoardProfile`、`SwitchPlan`、`compile_plan`、`verify_plan`、`route plan`（#20） | ✅ 已完成 |
| **M3** | JF2 主机侧：事务式下发、`DeviceClient`、`host sw apply/status/allopen`，丢帧/重复/断连/租约故障注入测试 | 📅 计划 |
| **M4** | 固件核心（可移植 C）：JF2 解析、先断后通提交、故障全断状态机、CH446Q 驱动接口，与 Python 共用协议向量 | 📅 计划 |
| **M5** | 主机与固件核心在 PC 上端到端联调，随机路由序列逐步核对开关影子状态 | 📅 计划 |
| **M6** | 上板 LED：F103 UART+DMA、WS2812、看门狗，16 颗 LED 逐颗点亮 | 📅 计划 |
| **M7** | 单片 CH446Q：逐点通断、时序核对、首份校准 | 📅 计划 |
| **M8** | 双芯片 + 共享 Y：完整 V0.1 拓扑实测，万用表记录连通与隔离 | 📅 计划 |
| **M9** | 安全：租约、看门狗、紧急全断上板实测 | 📅 计划 |
| **M10** | 扩展：MCU 夹持区、`host serve`、电源轨声明与 `RAIL_CONFLICT` 检查 | 📅 计划 |
