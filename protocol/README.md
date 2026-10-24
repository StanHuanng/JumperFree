# 协议与共享向量

JF2 主机/设备规范见 [jf2.md](jf2.md)，共享测试向量位于 [vectors/](vectors/)。

`protocol/vectors/jf1.vec` 是从当前 Python `MockTransport()` **实际执行并记录**的 JF1 规范冻结基线，后续供 Python MockDevice、C 单元测试和 `jf_sim` 共用。预期应答已写死；测试不得现场调用 Mock 生成预期值。若实现与向量不一致，应检查兼容性，不能直接重新生成向量来通过测试。当前仅完成软件验证，未经硬件验证。

## 文件与执行约定

文件用 UTF-8 保存，行尾接受 LF 或 CRLF，最后一行可以没有行尾。解析时按 LF 划分物理行，并只去除各行末尾一个 CR。行号从 1 开始，`VecStep.line` 记录请求所在物理行。每个文件使用**一个全新设备实例**；步骤按文件顺序执行，文件内状态延续，文件间互不共享。

| 行格式 | 含义 |
| --- | --- |
| `# comment` | 首字符为 `#` 的整行注释，可含中文 |
| 空行 | 空串或仅含 ASCII 空格、TAB 的行，忽略 |
| `> <request>` | 前缀为 `>` 加一个 ASCII 空格，其后的全部字符是请求载荷 |
| `< <expected response>` | 前缀为 `<` 加一个 ASCII 空格，其后的全部字符是预期应答载荷 |
| `@ <name> <args>` | 白名单故障/时间指令，见下节；不产生线上应答 |

表中的尖括号是占位说明，不写入载荷。前缀前不可缩进。前缀的一个空格不属于载荷；其后首尾空格都属于载荷，不能提前 trim。没有行内注释，载荷中的 `#` 是普通字符。`> ` 后允许空载荷，用于测试空请求；应答至少有一个 token。

忽略注释和空行后，每个 `> ` 必须紧跟**恰好一个** `< `，不可连续请求、孤立应答或多余应答；文件结束时不得有未配对应答的请求。JF1/JF2 均为一次请求对应一次应答。指令仅可出现在完整请求/应答对之间；不能插入 `>` 与 `<` 中间。

## 载荷字节与转义

未转义载荷只允许可打印 ASCII 字节 `0x20..0x7e`。反斜杠开始转义，C 解析器可逐字节扫描并按下表输出；中文只能出现在注释中，非 ASCII 请求需使用十六进制转义。

| 源文本 | 解码字节 |
| --- | --- |
| `\\` | `0x5c`，一个反斜杠 |
| `\r` | `0x0d` |
| `\t` | `0x09` |
| `\xHH` | 恰好两位十六进制数字，允许 `0..9`、`a..f`、`A..F`，输出一个字节 |

其他转义（包括 `\n`、`\0`、`\XHH`）、孤立反斜杠、缺位或非法十六进制均为语法错误。转义不递归：`\\x41` 解码为四个字节 `\x41`，`\x414` 解码为 `A4`。解码后载荷**不能包含 LF**，因此 `\x0a` / `\x0A` 也必须拒绝，避免一步产生多帧。

请求解码为 bytes 后追加**恰好一个 LF (`0x0a`)** 发送；例如 `> JF1 STATUS\r` 发送 `JF1 STATUS` 加 CRLF。`VecStep.request` 已包含追加的 LF，消费方直接 write，不再追加。请求允许任意其他转义字节，包括 `\x00`、`\xff`，以覆盖坏帧；C 实现须保存显式字节长度，不能用 `strlen` 处理解码结果。文件本身的 CRLF 不属于载荷，需在线上发送的 CR 必须转义。

预期应答载荷同样可能含 `\x00` 等控制字节；C 解析器解析预期 token 时也必须按显式长度处理，不能使用 `strlen`。

## 应答匹配

预期应答解码后必须是 ASCII。预期和实际应答均按 ASCII 空白（SP、HT、CR、VT、FF）切成非空 token，忽略首尾空白及连续分隔符。实际应答先去掉末尾一个 LF（允许调用方已去掉 LF）；若其余位置还有 LF 或含非 ASCII 字节，匹配失败。CRLF 应答的 CR 会作为空白处理。这是向量比较规则；JF1 自身的单空格帧规则仍由协议解析器负责。

token 数量必须完全相同，区分大小写，逐个比较：

- 预期 token **恰好为 `*`**：匹配任何一个非空 token，不匹配零个或多个 token。
- 预期 token 为 `key=*`：`key` 为非空 token 前缀；实际 token 必须以相同的 `key=` 开头，且 `=` 后至少有一个字节。例如 `boot=*` 匹配 `boot=ab12`，不匹配 `boot=` 或 `other=ab12`。
- 其他 token 完全相等才匹配。`abc*`、`key=*suffix`、`=*` 均为字面值，不进行子串或正则匹配。通配识别在转义解码后进行，`\x2a` 也表示通配 `*`。

`jf1.vec` 没有易变字段，所以不使用通配符；每条 STATUS 都写出完整 48 个 RGB 值。它覆盖全部命令与 Clip、RGB 边界、CRLF、所有当前可在线上产生的错误消息及其校验优先级、512 字节边界，并在坏请求后检查状态未变。无 LF 的输入不会触发 Mock 应答，不属于本格式的一请求一应答步骤；传输分段、关闭后 I/O 等行为由现有 transport 单元测试覆盖。

## Python 接口与错误

`jumperfree.core.vectors.parse_vec(text, name="<vec>")` 返回不可变 `tuple[VecStep | VecDirective, ...]`；`match_response(step.expected, actual_line)` 返回布尔值。解析器不做文件 I/O；测试消费方读取 UTF-8 文本。

所有语法错误抛出 `VecError(JfError)`，`code="VEC_SYNTAX"`，`details` 含 `name`、`line`，消息也含文件名和行号。错行报告当前物理行；到 EOF 才发现缺应答时，报告悬空请求行。未知指令包含原始指令文本，不能静默忽略。

## 指令与 JF2 固定向量

解析器只接受下列精确语法（单空格，无尾随空格），未知指令/参数/位置一律 VEC_SYNTAX。`VecDirective(name, args, line)` 为冻结对象，line 是指令物理行号。harness 按顺序执行，无 write/readline、无应答匹配；下一请求才观察结果。MockDevice 使用 `directive(step.name, step.args)`；C harness 应实现相同模型动作。

| 指令 | C/Python harness 语义 |
| --- | --- |
| `@ advance_ms <n>` | n 为无符号规范十进制（允许 0）；增加虚拟时间 n 毫秒，立即检查租约（等于到期点即过期）。向量实例应使用虚拟时钟；不要真实 sleep |
| `@ fault hw` | 注入持续硬件源，立即全断/中止事务、epoch+1、SAFE，锁存 HW_FAULT=0x4 |
| `@ fault_source_gone hw` | 源消失，锁存仍在；后续 FAULT CLEAR 才清除 |
| `@ reboot por` | 清影子/LED/会话/缓存/输入输出，epoch=0，生成新 boot，uptime 归零；活跃 HW 源保留 |
| `@ reboot iwdg` | 同上，reset=iwdg，另锁存 WDT_RESET=0x20；新 HELLO 前需清故障才可 arm |
| `@ interrupt_next_commit` | 下一次通过全部校验的 COMMIT 中断，全断/epoch+1/SAFE，锁存 0x2、结果 ABORTED，ERR COMMIT_INTERRUPTED；未通过校验不消费标记 |

JF2 文件中仅 boot/up 使用 `key=*`；CRC、位图、tag、caps、版本、reset、状态和故障都固定。每个文件新建虚拟时间从 0 开始的 MockDevice。JF1 冻结向量同时对 MockTransport 与 MockDevice 运行，JF2 文件只对 MockDevice。JF2 的 CRC 均使用真实操作串计算后固定写入文件，不能沿用架构文档示意 CRC。BBM 向量故意先发送 make 再 break；Python 测试另检查 execution_log，C harness 应检查实际 OFF/ON 调用顺序。boot 通配不证明重启发生，Python 另有新旧 boot 不相等测试，C harness 也应检查重启时生成新 boot。
