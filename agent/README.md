# Agent tools

工具 API 位于 [`python/src/jumperfree/agent/`](../python/src/jumperfree/agent/)，
通过 `import jumperfree.agent` 使用。本目录仅作指引。

接口、共享 Mock 会话、连续调用示例及 MCP 注册建议见
[`docs/agent/tools.md`](../docs/agent/tools.md)。v0.1 仅提供四个 Mock 工具，
没有自然语言解析或真实硬件集成；`verified=false`，进程退出丢失会话。
