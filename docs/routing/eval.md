# 小拓扑 Steiner / 重排对比

运行现有评估命令即可同时比较纯 Steiner 与有界重排，无需新增开关：

```bash
python3 scripts/run_eval.py --seed 7 --trials 100 \
  --max-moves 8 --max-states 2000 --max-rip-nets 3 --out out/eval.json
```

评估覆盖 1–3 颗芯片、2–4 条 X 线、2–3 条 Y 线，共 18 个拓扑、36 行路由器结果。
`--trials` 是每个拓扑的操作数，包含 create 和 delete；`attempts` 只统计 create。
每对路由器使用完全相同的有种子操作流，拒绝请求不会改变后续请求或删除操作；删除未成功
创建的 net 会跳过。各路由器独立维护接受请求后的状态，oracle 对比基于各自创建前的状态。
JSON 记录 `seed`、`trials` 和 `budgets`；使用相同版本代码及这些参数可以复现完整报告。

每行通过 `router` 和 `topology` 标识对比对象：

- `accepted` / `rejected`：接受和拒绝的创建数，合计为 `attempts`；`success_rate` 为接受比例。
- `total_moves` / `avg_moves`：累计和每次创建平均的 `C_move`，即既有 net 的线与交叉点集合
  对称差数量；新 net 自身资源不算移动。普通创建及失败请求的移动数为 0。
  `avg_states` 是每次创建平均探索状态数，包含失败搜索。
- `compared_to_reference` / `compared_to_true_exhaustive`：可比较的 `cases`、
  `agreements`、`disagreements` 和 `agreement_rate`。未启用 oracle 时 cases 为 0，比例 0
  表示没有样本。`budget_misses` / `true_budget_misses` 计数 oracle 可行但路由器拒绝的请求；
  对纯 Steiner 也保留这些字段，并不表示它执行了预算搜索。`timeouts` 当前固定为 0。
- `shorts_found` / `line_exclusivity_violations`：电气短接和线独占违规数，应均为 0。

邻域 oracle 默认只比较节点数不超过 8、既有 net 不超过 4 的实例，穷举至多 2 个既有 net
的撕除组合和重路由顺序，每条路由仍用 Steiner。它没有路由器的移动/状态预算限制，但也不是
全局最优。真穷举仅限单芯片、交叉点数不超过 6、加入新 net 后不超过 3 个 net。
详细边界见报告 `reference` 和[软件路由概览](overview.md)。

确定性反例 `tests/routing/fixtures/eval_shared_link_pressure.json` 由单元测试加载：本地 net
先占用唯一共享 Y 链路，跨芯片请求需要移动它才能成功。公共增删/复用操作流包含 6 次创建，
纯 Steiner 接受 3 次；重排使用 `max_moves=7,max_states=1,max_rip_nets=1` 时全部接受，
累计移动 14。移动预算降为 6、状态预算降为 0 或撕网预算降为 0 时均有 3 次可恢复遗漏。
黄金报告使用 seed=1、trials=2 和默认预算；反例回归可单独运行：

```bash
python3 -m unittest tests.routing.test_eval
```

以上结果均为**纯软件模型评估**，不涉及 CH446Q 开关控制，也不代表硬件或 PCB 已验证。
