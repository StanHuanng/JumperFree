# agent/

这里以后放置 Agent Skill。

Agent 负责把用户的自然语言需求转换为 Netlist，并调用 `python/` 提供的路由、连接计划与
主机通信接口。Agent 不直接操作 CH446Q 寄存器，也不自行决定开关闭合方式。

当前目录只有说明文件，尚未实现 Agent Skill。
