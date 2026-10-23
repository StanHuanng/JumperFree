# 当前 Python 分层

本页记录 V0.1 软件当前实现；拓扑、接线、亮度与串口参数均未经过硬件验证。

| 包 | 当前职责 | 允许依赖的 JumperFree 包 |
| --- | --- | --- |
| `core/` | SCHEMA 常量、规范 JSON、zlib 兼容 CRC32、JfError 和新错误信封 | 无其他包 |
| `routing/` | 拓扑、确定性路由、重排、短接检查与软件评估 | `core` |
| `board/` | `compile_led_plan`、冻结的 `LedPlan`、固定指引调色板与 `plan_guide` | `core`、`routing` |
| `host/` | 冻结的 JF1 编解码、Transport、HostSession 与旧 CLI 适配器 | `core` |
| `service/` | 状态文件读取、板级编译与设备会话的应用编排 | 上述各包 |

同包内部可以导入；`core` 不依赖其他 JumperFree 包，`routing` 不依赖 `host`，`routing` 和 `host` 均不得依赖 `service`、`board`、`device`。`board` 不依赖 `host`，不执行 I/O。顶层 `cli.py` 负责命令分发，只有应用服务层组合路由意图与设备调用。`tests/test_layering.py` 用标准库 AST 扫描绝对和相对导入，并以合成违规源码验证门禁。

现有 `routing/cli.py` 的状态文件读写与旧数据加载器暂保留，尚未全部迁入服务层；路由算法本身仍是纯计算。`host/cli.py` 保留 argparse 定义与普通 JF1 命令适配。实际串口 I/O 在 Transport 中。

`host guide` 的参数仍由 `host/cli.py` 定义，顶层 `jumperfree/cli.py` 将其分发给 `service/guide.py:run_guide`。服务先读取并校验状态，调用 `board/led.py:compile_led_plan`（通过 `plan_guide` 获取旧 JSON 列表），再用 HostSession 发送 `CLRALL` 和按 Clip 编号排序的 `SET`，最后关闭连接。board 的 `LedPlanError` 在服务层转换为原有 HostError 格式；事务失败时保留主错误，关闭失败嵌入 `details.close_error`。

`LedPlan.brightness_cap=64` 仅为记录占位，**待实测**；V0.1 不裁剪或缩放 RGB，指引仍使用原有八色调色板。`plan_guide` 与 `GUIDE_PALETTE` 从 `jumperfree.board` 导入，host 不再导出它们。

`core.error_envelope` 提供新命令将来使用的 §7.1 信封，目前未接入旧 CLI。现有 `ROUTE_JSON`、`HOST_JSON`、状态文件和 JF1 帧格式保持原样。

`device/` 留待 M3（JF2 主机侧）；BoardProfile、SwitchPlan 与配置文件留待 M2，固件留待 M4。当前没有开关下发、硬件保护或电气连接验证能力。V0.1 不预留 Tester 探针/ADC，物理验证依靠后续人工万用表记录。
