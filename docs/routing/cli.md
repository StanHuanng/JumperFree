# Stage-1 路由 CLI

所有命令默认读写 `out/routing-state.json`，也可用 `--state PATH` 指定其他文件。每次执行输出
一行人类摘要，以及一行键排序、紧凑编码的 `ROUTE_JSON {...}`。成功退出 0；路由或状态错误
退出 1，并在 JSON 的 `error.code` 中给出原因。

```bash
python3 -m jumperfree route init --nodes 16 --seed 7 --capacity 1
python3 -m jumperfree route create --net power --nodes 0,3
python3 -m jumperfree route create --net signals --nodes 5,8,12
python3 -m jumperfree route query
python3 -m jumperfree route query --net signals
python3 -m jumperfree route list
python3 -m jumperfree route delete --net power
```

`init` 会覆盖所指向的状态文件并创建空模型。`create` 是原子的：成功才保存，失败不修改状态。
`delete` 释放 net 的资源；`query` 不带 `--net` 时返回全部 occupancy，带参数时返回单个 net；
`list` 返回按 net ID 排序的列表。

本 CLI 没有 `rearrange` 命令，也不驱动 CH446Q 或其他硬件。节点数必须由调用者显式配置；真实
Jumperless 节点数仍待后续确认。

## 交叉点资源图命令

交叉点路由使用独立的默认状态文件 `out/crossbar-state.json`：

```bash
python3 -m jumperfree route xbar init --topology small --chips 3 --x-lines 4 --y-lines 4
python3 -m jumperfree route xbar create --net span --nodes N0,N4,N11
python3 -m jumperfree route xbar create --net extra --nodes N1,N5 --rearrange --max-moves 8 --max-states 2000
python3 -m jumperfree route xbar query
python3 -m jumperfree route xbar query --net span
python3 -m jumperfree route xbar delete --net span

# 也可载入包含 chips、node_attachments、inter_chip_links 的 JSON 拓扑
python3 -m jumperfree route xbar init --topology-file topology.json --state state.json
```

`small_topology` 会在每条 X 线上挂接一个节点，因此跨芯片路径可能需要把中继芯片上的节点也加入 net。

所有 xbar 命令沿用一行摘要加一行排序键 `ROUTE_JSON` 的输出约定。状态保存每个 net 的成员
节点、独占芯片线和闭合交叉点；查询同时给出每个 net 的交叉点 hop 数和总体线占用。
`--rearrange` 默认关闭；启用后仅在普通建网返回 `NO_FREE_PATH` 时进行有界局部重排，成功输出
会在 `ROUTE_JSON.rearrange` 中包含 move 数、实际改变的 net 和探索状态数。

