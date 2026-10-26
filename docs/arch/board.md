# V0.1 板级计划

板级层提供纯、确定性的开关计划编译与校验，不执行设备 I/O；JF2 事务由 host 执行，超过单事务容量的计划尚不支持拆分下发。所有配置均未经硬件验证，不能把软件测试结果当作物理连通证据。

## 配置与身份

逻辑目录 `boards/v01/` 随包分发，实际位置为 `python/src/jumperfree/board/boards/v01/`，通过 setuptools package-data 纳入安装包。默认 `BoardProfile.load()` 从模块旁加载，安装后无需仓库工作目录。`BoardProfile.load(path)` 和 CLI `--board-dir DIR` 可加载外部目录，其内必须有以下五个文件：

| 文件 | 用途 |
| --- | --- |
| board.toml | `jf.board/1`、board_id=v01、rev、四段独立 verified 信息 |
| topology.json | `v01_topology()` 的规范导出；单测断言内容相等 |
| pinmap.toml | F103、UART、CH446Q、LED 分配占位，均为 TBD/unassigned |
| limits.toml | max_ops=256、brightness_cap=64、lease_default_ms=0、t_gap_ms=1，全部待实测 |
| calibration.json | `jf.calib/1`，空 measurements 与 known_bad_crosspoints |

加载是显式文件 I/O 边界；加载后的冻结、带 slots 的 BoardProfile 保存规范 JSON 字符串，`topology` 属性返回独立副本。编译、校验与影子转换不读文件。芯片映射按假设 A4：C1→0，C2→1，尚未实测。

`topology_id = sha256(canonical_json({"topology": topology, "known_bad_crosspoints": points}).encode("utf-8")).hexdigest()[:16]`。坏点使用 `[chip_index,x,y]` 数组，排序、去重后计算身份；坏点顺序不改变身份。任何坏点集合变化都改变身份。

**DEFER：坏点从资源图移除。** 现有 routing 无过滤交叉点的公开钩子，本次清理不改路由算法。board 的 `reject_bad_crosspoints` 拒绝使用坏点的状态/计划并返回 `PLAN_BAD_CROSSPOINT`；不能宣称路由器已自动避让。未来增加图过滤后仍须保留计划检查。

## 类型与标签

`Xp(chip,x,y)`、`SwitchOp(xp,on,tag)`、`SwitchPlan` 均为冻结、带 slots 的数据类。chip 为 0..1、x 为 0..7、y 为 0..7；X8..X15 在 V0.1 未接线，返回结构化 `BAD_ARG`。`Xp.bit()` 为 `x*8+y`。SwitchOp 标签范围为 0..15；实际闭合 net 使用 1..15，0 表示无标签。

SwitchPlan 包含 schema=`jf.switch-plan/1`、base_epoch、topology_id、breaks、makes、expect_crc、disturbed_nets、tags。base_epoch 为 uint32，与 JF2 一致；设备 epoch 是否匹配由 host 执行前检查。操作按 `(chip,x,y)` 排序，`to_wire_tokens()` 先返回全部 `-c:x:y`，再返回全部 `+c:x:y@tag`。`to_json()` 返回可 JSON 序列化的字典，操作记录为 `{chip,x,y,on,tag}`；外层用 `core.canonical_json()` 以排序键、无额外空白输出。expect_crc 是目标影子 CRC 的整数值，布局见 [shadow.md](shadow.md)。标签表复制为只读映射。

标签存于状态的可选 `meta.tags`，不修改旧状态格式的其他键，读取方忽略未知键。`net_tags(state)` 优先读取仍存在 net 的旧标签，缺少标签时按 net ID 字典序分配最小空闲值；完全没有 meta 时即按字典序 1..n。删除 net 后遗留的标签项不再占位。

编译时，旧、新状态共有 net 保留旧标签，新 net 按字典序取得最小空闲标签。输入 new 中的标签不能覆盖这一规则。超过 15 个 net 返回 `PLAN_TOO_LARGE`。编译不修改输入：调用方用 `state_with_tags(new, plan.tags)` 获取带 `meta.tags` 的目标状态副本，成功应用后由后续 service 持久化。未知 meta 键保留，未使用的历史标签可以回收。

## 编译与重放

`compile_plan(old,new,prof,base_epoch=0)` 要求两状态拓扑相同（否则 `PLAN_TOPOLOGY_MISMATCH`），且与 profile 相同（否则 `PLAN_BOARD_MISMATCH`）。编译比较每个物理交叉点的 **net ID 与标签**：均相同则不下发，归属或标签变化则先断再通。`disturbed_nets` 为共有 net 中交叉点集合发生变化者的排序 ID 列表，表示重排可能打断在线电路。

`verify_plan(old,plan,prof)` 从旧影子重放：严格检查 break 原本闭合且非零 tag 与旧影子一致（0 不指定旧标签）、make 原本断开、阶段合法、topology_id 匹配、完整影子 CRC 匹配、标签归属合法，并对每条占用的 X 线路建立虚拟节点后调用 `routing.crossbar.find_shorts`，覆盖外部配置中未挂接节点的线路。任何重放不一致统一为 `PLAN_VERIFY_FAILED`。编译还独立比较重放影子与目标影子，并用目标声明的 net 成员再检查短接。超过 `limits.max_ops` 返回 `PLAN_TOO_LARGE`；拆分和跨事务中间态检查尚未实现。

校验返回 `{"nets": {net_id: {"crosspoints": [["C1",x,y],...]}}, "meta": {"tags": {net_id: tag}}}` 视图。`state_crosspoints_from_shadow(shadow,prof,tags)` 提供相同反向映射供后续 adopt-device 使用；影子无法恢复意图、声明成员或路由 lines，因此不伪造完整 RoutingState。

BBM 单调性：break 阶段只删除 old 的边；break 结束后保留的边都是 target 的边；make 阶段仅加入 target 的边。每个前缀因而是 old 或 target 的子集，也必定是二者并集的子集。两个端点状态都无短接时，各前缀也无短接。测试覆盖往返、每个前缀的安全与字节级确定性。

## verified 与命令行

board.toml 的 topology、pinmap、limits、calibration 四段均参与 profile，当前全为 `verified=false`，没有证据。非 false 只接受 `"bench"`、`"hil"`，要求 evidence 指向板目录内 `docs/hw/` 下的已有文件；不得逃出目录。工具不得自动翻转 verified。

在仓库的 `python/` 目录设置 `PYTHONPATH=src` 后运行：

```sh
python -m jumperfree route xbar init --topology v01 --state out/new.json
cp out/new.json out/old.json
python -m jumperfree route xbar create --net a --nodes Clip0,Clip15 --state out/new.json
python -m jumperfree route plan --new out/new.json --base-epoch 0
python -m jumperfree route plan --old out/old.json --new out/new.json --board-dir src/jumperfree/board/boards/v01
```

未提供 `--old` 时使用与 new 拓扑相同的空状态。CLI 只读文件，不保存标签或操作设备。使用未验证配置时在摘要前输出：

```text
警告：拓扑/引脚未经实测（verified=false）
PLAN OK breaks=0 makes=2 crc=<8位小写hex>
PLAN_JSON <规范计划JSON>
```

`--require-verified` 使当前出厂配置返回 `BOARD_UNVERIFIED`，不输出横幅。失败输出 `PLAN FAIL <CODE>: <message>` 和 `PLAN_JSON {"error":...,"ok":false}`。信封由 `core.error_envelope` 生成：板级错误 layer=plan，路由状态错误 layer=routing，retryable=false、state_changed=no。成功退出 0，失败退出 1，argparse 参数错误退出 2。子命令在顶层 CLI 接线，routing 不导入 board。
