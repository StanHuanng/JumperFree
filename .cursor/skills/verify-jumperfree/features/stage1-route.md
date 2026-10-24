# Stage-1 全连接布线

用户在终端里初始化一张全连接路由状态表，创建/删除电气网络（net），并查询占用情况。不涉及交叉开关芯片拓扑。

## Sub-features

- `stage1-init` 用节点数与 seed 生成空状态文件
- `stage1-create` 为 net 选择连通成员并写入状态
- `stage1-query-list` 查询占用或列出已布 net
- `stage1-delete` 删除 net 并释放资源

## How to get to it (user POV)

- 运行 `python3 -m jumperfree route init --nodes <n> --seed <s> --state <path>`
- 运行 `python3 -m jumperfree route create --net <name> --nodes <i,j,...> --state <path>`
- 运行 `python3 -m jumperfree route query [--net <name>] --state <path>`
- 运行 `python3 -m jumperfree route list --state <path>`
- 运行 `python3 -m jumperfree route delete --net <name> --state <path>`

## Driving it with jumperfree CLI

Preconditions:

- `helpers/launch.sh` 已成功，`$JF_VERIFY_STATE_ROOT/.ready` 存在
- `helpers/doctor.sh` 通过
- `$JF_VERIFY_STATE_ROOT` 下尚无 `stage1-routing-state.json`（或接受覆盖）

- **推荐一键。** 运行 `helpers/drive-stage1-route.sh`。退出码 `0`，且 `$JF_VERIFY_EVIDENCE_DIR/stage1-route/PROOF.txt` 含 `status=PASS`。
- **手工等价。** 设 `STATE=$JF_VERIFY_STATE_ROOT/stage1-routing-state.json`，依次：
  - `python3 -m jumperfree route init --nodes 8 --seed 7 --state "$STATE"` → 摘要含 `ROUTE init`，下一行 `ROUTE_JSON`
  - `... route create --net NET_1 --nodes 0,1,2 --state "$STATE"` → `ROUTE create net=NET_1`
  - `... route create --net NET_2 --nodes 3,4 --state "$STATE"` → `ROUTE create net=NET_2`
  - `... route list --state "$STATE"` → 输出中同时出现 `NET_1` 与 `NET_2`
  - `... route delete --net NET_1 --state "$STATE"` → `ROUTE delete net=NET_1`
  - `... route list --state "$STATE"` → 仍有 `NET_2`，且不再有 `NET_1`
- **Proof.** 保留 transcript 与 `final-state.json`（helper 会拷到 evidence）。第二次 `list` 是用户可见的结果状态。

## Gotchas

- 省略 `--state` 时默认写仓库 `out/routing-state.json`，会污染工作树；验收必须显式传隔离路径
- `create` 的 `--nodes` 是逗号分隔的**整数**下标，不是 Clip 名
- 失败时进程退出码非 0，且 stdout 仍可能有 `ROUTE_JSON` 错误信封；以 `ok` 字段与退出码为准
