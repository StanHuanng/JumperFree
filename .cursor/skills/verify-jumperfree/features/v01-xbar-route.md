# V0.1 交叉开关布线

用户按 V0.1 板拓扑（两颗 16×8 交叉开关、Clip0–15）初始化资源图，用 Steiner 多跳为 Clip 节点建网，并查询占用。

## Sub-features

- `xbar-v01-init` 载入 `v01` 拓扑并写出空交叉开关状态
- `xbar-create` 在空闲资源上为 net 建树
- `xbar-query` 查看整体占用或单个 net
- `xbar-delete` 删除 net

## How to get to it (user POV)

- `python3 -m jumperfree route xbar init --topology v01 --state <path>`
- `python3 -m jumperfree route xbar create --net <name> --nodes Clip0,Clip15 --state <path>`
- `python3 -m jumperfree route xbar query [--net <name>] --state <path>`
- `python3 -m jumperfree route xbar delete --net <name> --state <path>`

## Driving it with jumperfree CLI

Preconditions:

- Launch + doctor 已通过
- 状态文件使用 `$JF_VERIFY_STATE_ROOT/v01-xbar.json`

- **Init.** `python3 -m jumperfree route xbar init --topology v01 --state "$JF_VERIFY_STATE_ROOT/v01-xbar.json"` → `ROUTE xbar init` + `ROUTE_JSON`
- **Create.** `... route xbar create --net a --nodes Clip0,Clip15 --state "$JF_VERIFY_STATE_ROOT/v01-xbar.json"` → `ROUTE xbar create net=a`
- **Query.** `... route xbar query --net a --state ...` → JSON 中 net `a` 的 nodes 含 `Clip0` 与 `Clip15`
- **Proof.** 把 create/query 的 stdout 存到 `$JF_VERIFY_EVIDENCE_DIR/v01-xbar-route/`，并复制最终状态 JSON。再 `query` 一次确认 net 仍在。

## Gotchas

- `--nodes` 这里是 **Clip 名字符串**（如 `Clip0`），与 stage-1 的整数下标不同
- `--topology v01` 与 `small` / `v5-scale` 行为不同；验收 V0.1 板路径时不要用 `small` 冒充
- 资源不足时返回结构化错误（如 `NO_FREE_PATH`），退出码非 0；这是有效否定证明，不要改成别的拓扑硬凑成功
