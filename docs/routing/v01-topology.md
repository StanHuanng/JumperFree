# JumperFree V0.1 软件拓扑

`jumperfree.routing.v01_topology.v01_topology()` 返回一个新的、可 JSON 序列化且经过
`validate_topology` 校验的拓扑字典，也可通过 `from jumperfree.routing import v01_topology`
调用。模型名为 `jumperfree_v01_software_topology`，表示 JumperFree V0.1 的设计接线。
`metadata.verified` 与每项 `hardware_assumptions[].verified` 都是 `false`，尚无硬件实测验证。

## 芯片与接线

16 个 Clip 对应 16 个独立电气节点，节点名称区分大小写。

| 资源 | 接线 / 数量 |
| --- | --- |
| C1、C2 | 两颗 CH446Q 风格芯片，每颗 16 条 X 线、8 条 Y 线 |
| Clip0–Clip7 | 依次接 C1 X0–X7 |
| Clip8–Clip15 | 依次接 C2 X0–X7 |
| X8–X15 | 两颗芯片均不接外部节点或芯片间固定链路 |
| 共享 Y 通道 | C1 Y0 ↔ C2 Y0，直到 C1 Y7 ↔ C2 Y7，共 8 条 |

每对同编号 Y 线通过固定接线成为同一电气通道；不同编号之间没有固定连接。
同一通道在两颗芯片上的 Y 线必须由同一个 net 独占，不能算成 16 条独立通道。
同芯片内的连接也会占用共享 Y 通道在另一颗芯片上的固定相连资源。

## 资源图与路由

通用 `build_resource_graph` 保留完整的 16×8 芯片矩阵，包括没有外接线的 X8–X15：

| 资源图项目 | 数量 |
| --- | ---: |
| 外部 Clip 节点 | 16 |
| 芯片线顶点 / 独占资源 | 48 |
| 总顶点 | 64 |
| 可控交叉点边 | 256 |
| 固定边 | 24（16 条 Clip 接线 + 8 条共享 Y 接线） |

这些计数是软件资源图的计数；未接线的 X8–X15 不会增加外部 Clip 或共享 Y 通道。
例如 `Clip0` 到 `Clip15` 的空状态确定性路由为：

```text
Clip0 — C1 X0 — [交叉点 C1 X0/Y0] — C1 Y0
                                          │ 固定接线
Clip15 — C2 X7 — [交叉点 C2 X7/Y0] — C2 Y0
```

该 net 闭合两个逻辑交叉点，占用两条 X 线和两条固定相连的 Y 线，其他 Clip 不加入 net。
软件测试还覆盖 8 个互不相交的跨芯片 Clip 对同时使用 8 条独立 Y 通道，并检查线独占和短接。

## CLI 与 Python API

```bash
python3 -m jumperfree route xbar init --topology v01 --state out/v01-state.json
python3 -m jumperfree route xbar create --net span --nodes Clip0,Clip15 --state out/v01-state.json
python3 -m jumperfree route xbar query --net span --state out/v01-state.json
python3 -m jumperfree route xbar delete --net span --state out/v01-state.json
```

初始化摘要包含 `lines=48`。每条命令输出摘要和 `ROUTE_JSON`，状态文件保存完整拓扑及未验证元数据。
`--topology-file` 优先于 `--topology v01`；`--chips`、`--x-lines`、`--y-lines` 仅用于 `small`，
不会改变 V0.1 的固定尺寸。`init` 会覆盖指定状态文件。

```python
from jumperfree.routing import crossbar_create_net, crossbar_init_state, v01_topology

state = crossbar_init_state(v01_topology())
state = crossbar_create_net(state, "span", ["Clip0", "Clip15"])
```

此功能只生成软件拓扑与路由状态，不涉及 Host LED 命令、CH446Q 寄存器编程、实际开关切换或 PCB。
CLI 其余约定见 [路由 CLI](cli.md)。
