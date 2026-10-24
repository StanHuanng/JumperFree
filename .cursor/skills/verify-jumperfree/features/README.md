# JumperFree 验收功能图

本目录是 JumperFree **用户侧行为**的验收来源。先读本索引，再按对应功能文件执行。主界面是 PC 上的 Python CLI（`python3 -m jumperfree`），不是网页。

## 基线前提

- 在仓库根目录执行 helper：`.cursor/skills/verify-jumperfree/helpers/launch.sh`
- 把 helper 打印的 `JF_VERIFY_RUN_ID`、`JF_VERIFY_STATE_ROOT`、`JF_VERIFY_EVIDENCE_DIR` 导出到当前 shell
- 先跑 `helpers/doctor.sh`，看到 `DOCTOR_OK doctor_complete`
- 所有 `--state` / `--new` / `--old` 路径必须落在 `$JF_VERIFY_STATE_ROOT` 下，不要写仓库自带的 `out/`
- 不要去驱动不是本次 `launch.sh` 建出来的状态目录

## 驱动约定

- 除非功能文件另有前提，一律从空状态目录开始
- 命令按字面执行；子命令名、`--mock`、拓扑名 `v01` 保持不变
- 通过 helper 或直接 `python3 -m jumperfree ...`（helper 里的 `jf` 等价）
- 成功看 stdout 标记：`ROUTE_JSON` / `PLAN_JSON` / `HOST_JSON`，以及对应的 `ROUTE`/`PLAN`/`HOST` 摘要行
- 改状态后要用 `query` 或 `list`（或保存下来的 JSON）再读一遍做证明
- 清理只删 scratch；证据留在 `evidence/<run_id>/`

## 证明与跳过

- 证明里要有「用户敲过的命令」和「事后可见状态」，不能只有最后一屏
- CLI 证明包含命令、stdout、stderr、退出码（helper 的 `transcript.txt`）
- 副作用证明：状态 JSON 被写入，且第二次读取与预期一致
- Host 路径在本 skill 里只用 `--mock`（内存 LED 对端）。这不能当作真串口或固件已通
- 某条入口走不通时，记下尝试过的命令和缺的前提；不要改用别的入口却声称原入口已验证

## 功能文件约定

每个功能文件：一级标题 + 一段用户可见行为说明，然后固定四个二级标题：

1. `Sub-features`
2. `How to get to it (user POV)`
3. `Driving it with jumperfree CLI`
4. `Gotchas`

只写用户路径、稳定句柄、所需状态、命令和可观察结果；实现细节留给代码审查。

## Features

- [Stage-1 全连接布线](./stage1-route.md) — `route init/create/query/list/delete`
- [V0.1 交叉开关布线](./v01-xbar-route.md) — `route xbar` + 拓扑 `v01`
- [开关计划](./switch-plan.md) — `route plan` 先断后通
- [Clip LED（内存对端）](./host-led-mock.md) — `host set/clear/status/guide --mock`
- [交叉开关局部重排](./xbar-rearrange.md) — `route xbar create --rearrange`
