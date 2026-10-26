# 路由 CLI

交叉点路由默认读写 `out/crossbar-state.json`，也可用 `--state PATH` 指定其他文件。每条命令
输出一行人类摘要，以及一行键排序、紧凑编码的 `ROUTE_JSON {...}`。成功退出 0；路由或状态错误
退出 1，并在 JSON 的 `error.code` 中给出原因。

```bash
python3 -m jumperfree route xbar init --topology v01 --state out/v01-state.json
python3 -m jumperfree route xbar create --net span --nodes Clip0,Clip15 --state out/v01-state.json
python3 -m jumperfree route xbar query --net span --state out/v01-state.json
python3 -m jumperfree route xbar delete --net span --state out/v01-state.json
```

`--topology v01` 选择 16 个 Clip、两颗 16×8 芯片和 8 条共享 Y 通道的
[V0.1 软件拓扑](v01-topology.md)。初始化摘要包含 `lines=48`。状态文件保存完整拓扑及未验证
元数据；`init` 会覆盖指定状态文件。

也可以通过 `--topology-file` 载入包含 `chips`、`node_attachments`、`inter_chip_links` 的
JSON 拓扑，文件内容优先于 `--topology`。`--topology small` 与 `--chips`、`--x-lines`、
`--y-lines` 仅用于测试和临时小拓扑，不是 v1.0 板级配置。

所有 xbar 命令沿用一行摘要加一行排序键 `ROUTE_JSON` 的输出约定。查询同时给出每个 net 的
交叉点 hop 数和总体线占用。状态先写入同目录临时文件，再替换目标文件；写入失败返回
`STATE_WRITE_FAILED`，保留原状态文件。

路由只生成软件状态，不驱动 CH446Q、不执行开关切换，也不涉及 LED 或 PCB。
