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

