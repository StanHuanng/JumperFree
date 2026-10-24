# 交叉开关局部重排

用户在普通 Steiner 建网失败（无空闲路径）时，为 `xbar create` 加上 `--rearrange`，允许有限度地挪动已有 net 后再插入新网，并在 `ROUTE_JSON` 里看到重排统计。

## Sub-features

- `rearrange-flag` 启用 `--rearrange` 以及 `--max-moves` / `--max-states` / `--max-rip-nets`
- `rearrange-stats` 成功时 payload 含 `rearrange` 统计对象

## How to get to it (user POV)

- `python3 -m jumperfree route xbar create --net <name> --nodes <...> --state <path> --rearrange`
- 可选：`--max-moves 8 --max-states 2000 --max-rip-nets 3`（均为默认值）

## Driving it with jumperfree CLI

Preconditions:

- Launch + doctor 已通过
- 需要一份**已经拥堵到普通 create 会失败**的状态，或使用 `scripts/run_eval.py` 一类负载间接观察；不要为了「必绿」而在空板上空跑一句带 `--rearrange` 的 create 就宣称重排路径已验证

- **弱证明（旗标可解析）。** 在空的 `v01` 状态下：  
  `... xbar create --net a --nodes Clip0,Clip1 --rearrange --state ...`  
  → 成功，且若实现把 `rearrange` 统计写入 JSON，记录之；若空板无需挪动，统计可能显示未 rip — 在 Gotchas 中如实记录，**不要**把「空板一次成功」写成「阻塞重排已验证」
- **强证明（阻塞后恢复）。** 构造或回放能触发 `NO_FREE_PATH` 的序列，再以相同净请求加 `--rearrange` 观察是否成功；把两次 stdout 一并存入 `$JF_VERIFY_EVIDENCE_DIR/xbar-rearrange/`
- **Proof.** 至少保留带 `--rearrange` 的命令行、退出码与 `ROUTE_JSON`；强证明还需保留失败基线

## Gotchas

- 默认参数：`max-rip-nets=3`、`max-moves=8`、`max-states=2000`
- 评测脚本 `scripts/run_eval.py` / `run_scale_eval.py` 是负载对比，不是本功能的用户入口；可作补充证据，不能替代 CLI 入口
- 若当前仓库缺少稳定的「必堵」小夹具，旅程应报失败并写明缺前提，而不是改需求降格
