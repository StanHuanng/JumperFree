# Agent tools

工具 API 位于 [`python/src/jumperfree/agent/`](../python/src/jumperfree/agent/)，
通过 `import jumperfree.agent` 使用。本目录仅作指引。

接口、共享 Mock 会话、连续调用示例及 MCP 注册建议见
[`docs/agent/tools.md`](../docs/agent/tools.md)。提供七个工具（含只读 `plan_net_change`、`guide_leds`、`all_open`）和 stdio MCP 服务
`python -m jumperfree.agent.mcp_server`；行为规则见 `python/src/jumperfree/agent/SKILL.md`。
没有自然语言解析或真实硬件验证；`verified=false`，进程退出丢失会话。
