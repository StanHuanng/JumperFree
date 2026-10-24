# Python 分层

本页记录当前 Python 实现；拓扑、接线、亮度与串口参数均未经过硬件验证。

| 包 | 职责 | 允许依赖的 JumperFree 包 |
| --- | --- | --- |
| `core/` | SCHEMA 常量、规范 JSON、CRC32、JfError、错误信封、影子布局与 BBM 操作助手、协议向量解析 | 无其他包 |
| `routing/` | 交叉开关资源图、16 节点拓扑、确定性建网、短接检查 | `core` |
| `board/` | BoardProfile、SwitchPlan、compile/verify、标签与影子转换、LED 指引编译 | `core`、`routing` |
| `host/` | JF1/JF2 编解码、Transport、HostSession、MockDevice | `core` |
| `service/` | 状态文件读取、板级编译与设备会话的应用编排 | 上述各包 |

同包内部可以导入；`core` 不依赖其他 JumperFree 包，`routing` 不依赖 `host`，`routing` 和
`host` 均不得依赖 `service`、`board`。`board` 不依赖 `host`，编译与校验不执行 I/O；
`profile.py` 的显式配置加载和 `board/cli.py` 的只读文件入口是 I/O 边界。顶层 `cli.py`
负责命令分发。`tests/test_layering.py` 用标准库 AST 扫描绝对和相对导入，并以合成违规源码
验证门禁。

主要流程为 **route → plan → host**：意图先经过纯路由，再经板级编译与计划校验，最后由 host
层会话发送 JF1/JF2 帧。当前 MockDevice 只验证软件行为，固件与真实 CH446Q 控制尚未实现。

`host guide` 的参数仍由 `host/cli.py` 定义，顶层 `jumperfree/cli.py` 将其分发给
`service/guide.py:run_guide`。服务先读取并校验状态，调用 `board/led.py:compile_led_plan`
（通过 `plan_guide` 获取旧 JSON 列表），再用 HostSession 发送 `CLRALL` 和按 Clip 编号排序的
`SET`，最后关闭连接。board 的 `LedPlanError` 在服务层转换为原有 HostError 格式；事务失败时
保留主错误，关闭失败嵌入 `details.close_error`。

板级配置随包存放于 `python/src/jumperfree/board/boards/v01/`，默认安装后可加载，也可指定
外部目录，详见 [板级计划](board.md)。共享影子布局位于 core，host MockDevice 无需导入 board。
当前没有真实开关下发、硬件保护或电气连接验证能力。
