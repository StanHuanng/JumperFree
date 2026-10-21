# Stage-1 路由模型

## 图与资源

模型节点编号为 `0..N-1`。在仿真中，每一对无序节点 `{u,v}` 之间有 `capacity` 个独占 link
slot，默认 capacity 为 1。这是 fully connected undirected **simulation graph**，不是 CH446Q
或任何 Jumperless PCB 的网表。

一个 net 至少包含两个不同节点。创建 net 时，路由器对成员节点之间的空闲候选 link 做稳定的
seed 排序，再用 Kruskal 方式选出 spanning tree。相同状态、seed 和请求始终得到相同 edge 选择。
每个 edge 记录 `u`、`v` 和 `slot`。

## 占用与隔离

- 一个 link slot 最多属于一个 net；
- 一个节点最多属于一个 net，因此不会借用另一 net 的节点作为中继；
- 每个成功 net 的 edge 必须把其全部成员连接成单一 connected component；
- `delete` 会一次释放该 net 的全部节点和 edge；
- `query` 报告总资源、已用资源、空闲资源和逐 net connectivity。

stage 1 只在当前空闲资源上求解，不重排既有 net。如果无法得到 spanning tree，返回
`NO_FREE_PATH`，其 details 中以 `RESOURCE_EXHAUSTED` 标明资源原因。其他稳定错误码包括
`NODE_OUT_OF_RANGE`、`NET_TOO_SMALL`、`NODES_ALREADY_IN_OTHER_NET`、`NET_ALREADY_EXISTS`
和 `NET_NOT_FOUND`。这些错误只描述仿真状态，不表示硬件不可能。

状态文件格式可由 [`examples/routing/stage1-seed-7.json`](../../examples/routing/stage1-seed-7.json)
查看；JSON 使用排序键和固定缩进保存。

