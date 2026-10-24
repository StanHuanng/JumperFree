# JF2 V0.1 主机与内存设备规范（M3a）

本规范与 `host/jf2.py`、`jf2_session.py`、`mock_device.py` 和固定 [JF2 向量](../../vectors/protocol/) 同步，供 M4 的 C 实现逐字节对齐。所有开关、故障与 BBM 都只是 Python 内存模型，**未经硬件验证**。没有固件、DeviceClient、应用服务或新 CLI。影子不能证明物理通断；无 ADC、探针或电流检测，不宣称过流保护。

## 帧、类型与兼容边界

请求为 `JF2 <命令 tokens>\n`，总长最多 512 字节（含 LF）；载荷只能是 ASCII `0x20..0x7e`，token 非空，以恰好一个空格分隔，无首尾空格。JF2 不接受 CRLF。十进制字段为规范 uint32：`0` 或 `[1-9][0-9]*`，最大 4294967295，不允许正负号、前导零。nonce、CRC、RGB 是十六进制字段，不受十进制前导零限制。命令区分大小写。

成功：`JF2 OK <命令> [回显参数] [key=value ...]\n`。错误：`JF2 ERR <CODE> <ASCII 消息> [f=<hex>]\n`。key 语法为 `[a-z][a-z0-9_]*`，值非空，可含 `=`；不能重复 key，不能在 key/value 后放位置参数。主机保留所有未知 key，调用方忽略不认识的字段。CRC 输出恰好八位小写十六进制，输入接受大小写；故障值为不补零的小写十六进制。

`encode_jf2(tokens)` 的参数不含 `JF2`；返回 bytes，并检查帧、已知命令数量和十进制字段。未知命令可编码，由设备返回 `UNSUPPORTED`。`parse_jf2_response` 返回冻结的 `Jf2Response(ok, cmd, args, kv, err_code, message)`：`cmd` 是一到两个字符串的 tuple，`args` 为回显字符串 tuple，`kv` 为只读映射。成功时 `ok=True, err_code=None, message=""`；与 JF1 一样，设备 ERR **直接抛** `HostError("DEVICE_ERROR", 消息, device_code=CODE)`，不返回 `ok=False` 对象。格式错误抛 `MALFORMED_RESPONSE`。消息保留故障尾字段，与 JF1 的错误信封规则一致。

JF1 的解析、HostSession、LED 值和错误字节永久冻结，MockDevice 直接复用 MockTransport 的 JF1 路径，两种协议共用 16 个 RGB 值。冻结的 jf1.vec 还包含 `JF2 STATUS`、`JF2 CLR -1`、`JF2  STATUS` 三种错误前缀测试。因此 **JF2 前缀加旧 LED 命令名 SET/CLR/CLRALL/STATUS**（前缀后一个或多个空格）仍走 JF1 拒绝路径，返回原 JF1 错误字节；这些名字不作为 JF2 命令。新协议用 `SW STATUS`、`LED BATCH`。其他未知 JF2 命令返回 JF2 UNSUPPORTED。此兼容例外必须在 C 分发器中保留。

## 开关 token 与影子

- 通：`+c:x:y@tag`，c=0..1，x/y=0..7，tag=1..15，均为规范十进制。
- 断：`-c:x:y`，不能带 tag；内部 SwitchOp 的 break tag=0。
- `parse_switch_op` / `encode_switch_op` 使用冻结的 Crosspoint / SwitchOp；编码也接受具有相同属性的板级对象，不导入 board。
- `commit_crc(tokens)` 是按帧 seq、帧内顺序拼接每个 `token + LF` 的 zlib 兼容 CRC32，返回整数。空序列为 0。

复用 [M2 影子格式](../arch/shadow.md)：每芯片 128 bit，bit=x*8+y，字节序为 little-endian。CRC 输入为 chip0/chip1 各 16 字节位图，然后每芯片 128 个 tag 的 nibble 打包（偶索引为低 nibble），共 160 字节。x=8..15 保留且为零。`SW STATUS` 的 c0/c1 各为 32 hex。`SW TAGS` 仅传每芯片 **已使用的 64 个交叉点**，t0/t1 各 32 字节 / 64 hex；每字节低 nibble 仍对应偶数 bit，保留的后 64 个 tag 不传，但仍参与影子 CRC。

## 状态、会话、时间与故障

启动：SAFE、空影子、epoch=0、LED 全零、无事务/缓存/HELLO，默认租约为 0。boot 是八位小写 hex。Python 的 `seed=0` 默认配合 `random.Random(seed).getrandbits(32)`；同 seed 的完整重启序列可重现。每次重启生成新值，时间从该次启动重新计算。C 自行产生 boot，向量将它视为易变字段。`reset_cause` 初始可注入 `por/iwdg/sw`。iwdg 启动锁存 WDT_RESET；活跃 HW 源在重启后仍锁存 HW_FAULT。

| 状态 | 命令权限 |
| --- | --- |
| SAFE | JF1 LED、HELLO/INFO/PING、SW STATUS/TAGS/ALLOPEN、FAULT、TXN QUERY、CFG GET、SELFTEST 0；1/2 始终 UNSUPPORTED |
| ARMED | 以上 + LEASE、LED BATCH、TXN BEGIN；没有暂存时 ADD/COMMIT 为 TXN_UNKNOWN，ABORT 可建立 ABORTED 结果 |
| STAGING | 以上只读/安全命令、HELLO、PING、LEASE、TXN ADD/COMMIT/ABORT/QUERY；LED BATCH / SELFTEST 0 为 BUSY；不同 id BEGIN 为 BUSY |
| COMMITTING | 仅内部同步过渡，不接受流水线，不向客户端暴露 |

本实现按 §3.3 的“任意状态”允许只读命令，按“HELLO 后”允许 STAGING 续租；§4.4 的 STAGING 简表不是排除这些读操作/续租的完整白名单。SAFE 中 LEASE、LED BATCH 和新切换请求返回 SAFE_MODE；历史 COMMIT/ABORT 的结果重放不执行开关，可在 SAFE 查询或重放。CFG SET 始终 UNSUPPORTED。未知子命令也 UNSUPPORTED。

HELLO 检查通过后中止未决事务并记 ABORTED，开启新会话，保留连接、epoch、租约设置和本次 boot 的最近结果。故障为 0 则进入 ARMED，否则 SAFE。**回复是 HELLO 之后的状态**，与架构方案 §3.4 示例中的 SAFE 不同。FAULT CLEAR 只清锁存位，不自动 arm；还需新的 HELLO。ALLOPEN 始终全断、中止事务、epoch+1、进入 SAFE；不清故障。每次成功 COMMIT 或 ALLOPEN 都增加 epoch，包括空 COMMIT 和重复 ALLOPEN；重放 COMMIT 不增加 epoch。这里“幂等”指开关/LED 效果；ALLOPEN 的 epoch 仍逐次增加。

`LEASE ms` 接受 0..600000，0 表示永久保持（D2）；非零使用最近成功接受 JF2 请求的时间。**所有成功 JF2 请求**（包括只读、查询、重放）刷新计时，ERR、坏帧、JF1 不刷新。达到 `now-last >= ms` 即到期，先全断/清 tag、中止事务、epoch+1、SAFE，再处理当前请求，锁存 LEASE_EXPIRED。在 SAFE 不重复触发到期；新的 HELLO arm 后重新计时。被动 UART 在 LEASE 0 时无法检测拔线。Mock 在请求、readline、advance_ms 时检查，单独读取 Python 属性不推进时钟。clock 返回毫秒，可为浮点；uptime 输出向下取整的毫秒。

| bit | 名称 | 行为 |
| --- | --- | --- |
| 0x1 | LEASE_EXPIRED | 租约到期锁存 |
| 0x2 | COMMIT_INTERRUPTED | 注入提交中断锁存 |
| 0x4 | HW_FAULT | 活跃的 hw 源不能清除 |
| 0x8 | POWER | 保留，无硬件 |
| 0x10 | OVERCURRENT | 保留，无硬件 |
| 0x20 | WDT_RESET | iwdg 重启锁存 |

任何故障均全断并进入 SAFE。非零掩码在下个响应尾部携带 `f=<hex>`；FAULT GET/CLEAR 自身始终含 f（包括 f=0），不会重复添加。FAULT CLEAR 清掉没有活跃源的锁存位；hw 源尚在时保留 0x4。caps 固定为 `led,sw,txn,lease`，永不含 overcurrent/probe。

## 命令与规范应答

以下均省略 `JF2 ` 和终止 LF；成功一律加 `OK `，非零故障按上节附 f。字段顺序也是线上顺序。

| 请求 | OK 后的内容 |
| --- | --- |
| `HELLO 2 nonce` | `HELLO fw=0.1.0 proto=2 caps=led,sw,txn,lease chips=2 board=v01 boot=<8hex> epoch=<n> state=<s> reset=<cause> nonce=<原样>` |
| `INFO` | `INFO` + HELLO 相同的 fw..reset 字段 + `brightness_cap=64 lease_default_ms=0 max_ops=256`（无 nonce） |
| `PING seq` | `PING seq up=<ms>` |
| `LEASE ms` | `LEASE ms` |
| `TXN BEGIN id base_epoch n_ops` | `TXN BEGIN id` |
| `TXN ADD id seq tok...` | `TXN ADD id seq` |
| `TXN COMMIT id n_frames crc` | `TXN COMMIT id epoch=<n> crc=<新影子8hex> opened=<断数量> closed=<通数量>`，历史重放附 `replayed=1` |
| `TXN ABORT id` | `TXN ABORT id` |
| `TXN QUERY id` | `TXN QUERY id result=OPEN/COMMITTED/ABORTED/UNKNOWN`；COMMITTED 再附原 epoch/crc/opened/closed |
| `SW STATUS` | `SW STATUS epoch=<n> crc=<8hex> c0=<32hex> c1=<32hex> state=<s>` |
| `SW TAGS` | `SW TAGS t0=<64hex> t1=<64hex>` |
| `SW ALLOPEN` | `SW ALLOPEN` |
| `LED BATCH n clip:rrggbb...` | `LED BATCH n` |
| `FAULT GET/CLEAR` | `FAULT GET/CLEAR f=<hex>` |
| `SELFTEST 0` | `SELFTEST 0 result=pass` |
| `CFG GET key` | `CFG GET key value=<十进制值>` |

nonce 为 1..8 个 hex 字符，大小写原样回显；proto 非 2 返回 UNSUPPORTED。SELFTEST 1/2 固定返回 UNSUPPORTED（D5/A5），其他 level 返回 BAD_ARG。CFG key 只支持 brightness_cap=64、lease_default_ms=0、max_ops=256；未知 key 返回 BAD_ARG。brightness_cap 只是初始配置值，Mock 保存原始 RGB，不模拟功耗、亮度限制或故障覆盖层，JF1 原始 RGB 行为不变。

LED n=0..16，必须与更新个数完全一致，clip=0..15 且不重复，RGB 恰好六个 hex 字符（接受大小写）。先验证全部再写入，任何错误都不改变 LED。n=0 合法空更新。JF2 操作 token 坐标越界/格式错误为 BAD_ARG，普通 uint 格式错误为 BAD_FRAME。

## 事务、校验优先级与执行

id、seq、base_epoch、n_ops、n_frames 为 uint32。主机在会话中使用单调 id，不复用已完成 id。设备暂存上限 256 操作；n_ops 可为 0。最近 **四个完成结果**按完成顺序保存（COMMITTED 与 ABORTED 共用容量），直到重启；OPEN 单独存放。反复 ABORT 同一 id 把其结果移到最新，不额外占位。被淘汰的 id QUERY 为 UNKNOWN。

BEGIN：先检查容量，再处理已有事务（同 id、相同 base/count 幂等；同 id 内容改变 TXN_SEQ_CONFLICT；不同 id BUSY），再拒绝缓存中完成的 id（TXN_UNKNOWN），最后比较 epoch。ADD 至少一个 token；验证所有 token，然后校验 seq。seq 从 0 连续；已有 seq 内容完全相同则成功，否则 TXN_SEQ_CONFLICT；跳号 TXN_SEQ_GAP；接受后累计超过 256 为 TXN_TOO_LARGE。ADD 不修改影子。声明 n_ops 与实际总数是否相等留到 COMMIT 检查。

COMMIT 首先处理历史成功结果重放（忽略本次合法形状的 n_frames/crc，不执行），否则要求 ARMED/STAGING 会话及匹配的 OPEN id。检查 CRC 格式八 hex，再检查 base_epoch，然后一次比较 n_frames、实际操作数与声明数、操作 CRC；任一不符 TXN_CRC。校验失败保留事务以便查询/中止，epoch/影子不变。

把全部 breaks 按原有相对顺序应用于副本，再把全部 makes 按原有相对顺序应用；break 必须原为通、make 必须该时点为断，否则 BAD_ARG。这允许同一交叉点先断后通以改 tag，但禁止同一阶段重复操作。输入在帧内 make 排在 break 前也不改变执行顺序。

在完整 target 上重建 `x_owner[(chip,x)]` 和共享的 `y_owner[y]`，每个闭合交叉点两端都只能属于同一个 tag；任一冲突 SAFETY_SHORT，影子不变。相同 tag 可以共享线。不试图识别主机谎报 tag 或用户外接电源短接。通过后进入内部 COMMITTING，先执行全部断，再全部通；`execution_log` 记录规范 token 的真实 BBM 次序，仅保留最近一次成功提交的记录。模型不模拟 1ms 电气间隔。成功后替换影子、epoch+1、ARMED，缓存成功结果。

注入的下一次提交中断只在所有校验通过后消费：即使原影子有连接也清零，epoch+1、SAFE、结果记 ABORTED，返回 COMMIT_INTERRUPTED 并锁存 0x2。失败校验不消费注入。ABORT 仅丢弃匹配暂存，影子/epoch 不变，恢复 ARMED；无此 OPEN id 时建立 ABORTED 结果（需要已 arm，或重放已有 ABORTED）；不能中止已经 COMMITTED 的 id，返回 TXN_UNKNOWN。HELLO、ALLOPEN、故障、到期把当前 OPEN 记 ABORTED。

通用错误优先级：帧/ASCII/数量/uint → 命令与权限 → 具体参数/事务 → 目标合法性 → 安全检查 → 执行。未知命令为 UNSUPPORTED。固定向量保存所有覆盖到的完整英文消息；其余实现中的固定消息如下，C 应照抄，不能翻译线上消息：

| CODE | message（按触发原因） |
| --- | --- |
| BAD_FRAME | `expected LF frame of at most 512 bytes` / `expected printable ASCII and single spaces` / `invalid JF2 prefix or command` / `incorrect argument count` / `missing subcommand` / `expected canonical unsigned integer` |
| BAD_ARG | `integer exceeds uint32` / `invalid nonce` / `invalid switch operation` / `lease exceeds 600000 ms` / `invalid selftest level` / `unknown configuration key` / `invalid LED count` / `invalid LED update` / `duplicate LED clip` / `CRC requires eight hex digits` / `break requires closed point; make requires open point` |
| UNSUPPORTED | `unsupported protocol` / `no sensing hardware` / `no maintenance mode` / `unknown command` |
| SAFE_MODE | `HELLO required without faults` |
| BUSY | `transaction open` / `another transaction open` |
| TXN_UNKNOWN | `transaction already committed` / `transaction id already finished` / `transaction not open` |
| TXN_SEQ_CONFLICT | `BEGIN content changed` / `ADD content changed` |
| TXN_SEQ_GAP | `expected next sequence` |
| TXN_TOO_LARGE | `max_ops is 256` |
| TXN_CRC | `frame count operation count or CRC differs` |
| EPOCH_MISMATCH | `base epoch differs` |
| SAFETY_SHORT | `line has different tags` |
| COMMIT_INTERRUPTED | `commit interrupted all open` |

SAFE 下拒绝命令不修改影子；所有校验错误均不执行开关。模型没有 POWER / OVERCURRENT / INTERNAL 的人为触发接口。

## 主机 session 与故障注入

`Jf2Session(transport, clock=time.monotonic, frame_timeout=0.3, retries=2)`；clock 返回秒。`request(tokens, n_ops=None)` 用锁保证一个未决请求；成功 BEGIN 自动记 n_ops，直接 COMMIT 可显式传 0..256 的 n_ops。每次发送（包括重试和 QUERY）前调用可选 reset_input。默认最多三次同字节发送，只在 TIMEOUT 重试。SerialTransport 的新 reset_input 调 pyserial.reset_input_buffer；JF1 readline 与 HostSession 不变。JF2 串口读取暂时把单次 timeout 缩短到剩余总 deadline，再恢复原配置；其他 Transport 的 readline 必须有有限超时。

每次尝试从发送前计算总 deadline；丢弃坏行、JF1、不同 cmd、id、seq、PING/LEASE/SELFTEST 参数、CFG key、LED count 和 HELLO nonce 的回复，持续读到截止。到点包括迟到的正确回复也 TIMEOUT。ERR 帧无 cmd/id（协议本身限制），不能做关联；清输入后收到的合法 JF2 ERR 直接 DEVICE_ERROR。输出来回无流水线/异步事件。短写为 SHORT_WRITE，OSError 为 TRANSPORT_ERROR，关闭后为 SESSION_CLOSED；close 幂等。

COMMIT deadline 为 `1 + 0.005*n_ops` 秒，仅发送一次；TIMEOUT 后发送 TXN QUERY（查询也按普通超时/重试）。COMMITTED 且信息完整时构造 TXN COMMIT 成功响应，保留返回字段并加 replayed=1；ABORTED 抛 COMMIT_UNRESOLVED，details.state_changed="no"；OPEN/UNKNOWN/未知状态/信息缺失抛同码，state_changed="unknown"。QUERY 自身超时、设备错误或断连也带 state_changed="unknown"，设备原码仍保留。未声称 QUERY 为 UNKNOWN 时设备一定没切换。

MockDevice 的 Python 注入 API：`advance_ms(n)` 增加非负整数时间偏移并立即检查到期；`fault("hw")` 使源活跃、全断/epoch+1/锁存；`fault_source_gone("hw")` 只移除源，不清锁存；`reboot("por"|"iwdg"|"sw")` 清会话、输入输出、LED、影子、epoch 和历史，但保留 hw 源；`interrupt_next_commit()` 标记下一次通过校验的 COMMIT 中断。`directive(name,args)` 对接共享向量，白名单与 C harness 约定见 [vectors/README](../../vectors/README.md)。`reset_input()` 只清待收应答，不改变设备状态或未完成请求的接收字节。
