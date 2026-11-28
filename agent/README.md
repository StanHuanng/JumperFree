# JumperFree Host（给本地 Agent 用）

JumperFree Host 是可安装的 Python 包 `jumperfree` 里的 stdio MCP 服务 `jumperfree-mcp`。
Agent 在一次对话开始时拉起它，对话期间它保持运行，对话结束就退出。Claude Code、Cursor、
Codex 共用这一个程序。

- 安装与注册：[`docs/agent/install.md`](../docs/agent/install.md)
- 工具、会话生命周期、分层：[`docs/agent/tools.md`](../docs/agent/tools.md)
- Agent 行为规则：[`python/src/jumperfree/agent/SKILL.md`](../python/src/jumperfree/agent/SKILL.md)

六个工具：`describe_board`、`get_state`、`plan_net_change`、`guide_leds`、`execute_plan`、
`all_open`。Agent 只提交网名和节点；Host 计算路径；板上固件执行。`verified=false`。
