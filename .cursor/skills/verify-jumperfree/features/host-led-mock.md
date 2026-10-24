# Clip LED（内存对端）

用户在没有串口板的情况下，用内存中的 LED 对端练习 `host set/clear/clear-all/status`，以及按路由状态点亮 Clip 的 `host guide`。

## Sub-features

- `host-set-clear` 单 Clip 设色与清除
- `host-status` 读取对端状态
- `host-guide` 根据交叉开关状态为各 net 分配颜色并下发

## How to get to it (user POV)

- `python3 -m jumperfree host set --clip <0-15> --rgb R,G,B --mock`
- `python3 -m jumperfree host clear --clip <n> --mock`
- `python3 -m jumperfree host clear-all --mock`
- `python3 -m jumperfree host status --mock`
- `python3 -m jumperfree host guide --state <xbar.json> --mock`

## Driving it with jumperfree CLI

Preconditions:

- Launch + doctor 已通过
- Guide 需要一份含至少一网的 V0.1 状态（见 `v01-xbar-route`）

- **Set.** `python3 -m jumperfree host set --clip 0 --rgb 255,128,0 --mock` → `HOST set OK` + `HOST_JSON`
- **Clear / status.** `... host clear --clip 15 --mock`；`... host clear-all --mock`；`... host status --mock` → 均为 `HOST ... OK`
- **Guide.** 先 `route xbar init --topology v01` 与 `create --net a --nodes Clip0,Clip15` 写到 `$JF_VERIFY_STATE_ROOT/guide.json`，再  
  `python3 -m jumperfree host guide --state "$JF_VERIFY_STATE_ROOT/guide.json" --mock` → `HOST guide OK`，`HOST_JSON` 的 data.plan 为非空列表
- **Proof.** 保存各命令 stdout；guide 的 JSON 中应能看到 clip 与 RGB 元组

## Gotchas

- `--mock` 与 `--port` 互斥；且 `--mock` 时不能带 `--baudrate` / `--timeout`
- 本 skill **不**验证真 USB-UART；需要板子与可选依赖 `pyserial` 时另开硬件验收
- `guide` 的 `--state` 必须是交叉开关状态，不是 stage-1 全连接状态
