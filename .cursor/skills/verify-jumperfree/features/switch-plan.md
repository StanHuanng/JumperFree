# 开关计划

用户把新旧交叉开关路由状态编译成先断后通的开关计划（`SwitchPlan`），并看到目标状态的 CRC。这是上板前的主机侧产物。

## Sub-features

- `plan-from-empty` 仅给 `--new`（旧状态视为同拓扑空图）
- `plan-diff` 同时给 `--old` 与 `--new`，只对差异下发
- `plan-banner` 板资料未实测时打印警告横幅

## How to get to it (user POV)

- 先有交叉开关状态（通常来自 `route xbar`）
- `python3 -m jumperfree route plan --new <new.json> [--old <old.json>] [--base-epoch N]`

## Driving it with jumperfree CLI

Preconditions:

- Launch + doctor 已通过
- 已按 `v01-xbar-route` 生成至少一个 net 的新状态；另存一份 init 后、create 前的文件作为 `--old`

- **准备。**  
  - `python3 -m jumperfree route xbar init --topology v01 --state "$JF_VERIFY_STATE_ROOT/plan-old.json"`  
  - `cp "$JF_VERIFY_STATE_ROOT/plan-old.json" "$JF_VERIFY_STATE_ROOT/plan-new.json"`  
  - `python3 -m jumperfree route xbar create --net a --nodes Clip0,Clip15 --state "$JF_VERIFY_STATE_ROOT/plan-new.json"`
- **Compile。** `python3 -m jumperfree route plan --new "$JF_VERIFY_STATE_ROOT/plan-new.json" --old "$JF_VERIFY_STATE_ROOT/plan-old.json" --base-epoch 0`  
  → 可能先打印 `警告：拓扑/引脚未经实测（verified=false）`，然后 `PLAN OK breaks=... makes=... crc=...` 与 `PLAN_JSON ...`
- **Proof.** 保存完整 stdout 到 `$JF_VERIFY_EVIDENCE_DIR/switch-plan/plan.stdout`；确认 `PLAN_JSON` 可被 `json` 解析且含 breaks/makes（字段名以实际 JSON 为准）。

## Gotchas

- 当前默认板资料 `verified=false` 时横幅是预期行为，不是失败
- `--require-verified` 在未实测板上会失败；不要为了绿而去掉该旗标的否定用例
- `route plan` 挂在 `route` 子命令下，不是顶层 `plan`
