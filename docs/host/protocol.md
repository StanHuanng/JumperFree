# JF1：Clip RGB LED 主机协议

JF1 是供 Stage-1 Host 和后续 F103 固件对接的行协议。当前实现及测试只覆盖 Python Host 和内存 Mock，不代表串口、固件、灯序或电气连接经过验证。UART 引脚与 BluePill 具体型号均为 **TBD**；其他待定接线见 [Host 使用说明](cli.md)。

## 冻结基线

[protocol/vectors/jf1.vec](../../protocol/vectors/jf1.vec) 是当前 MockTransport 行为的规范冻结基线，包含成功应答、完整 STATUS 与精确错误消息；格式及执行规则见 [共享向量说明](../../protocol/README.md)。后续 Python MockDevice、C 单测与 jf_sim 共用这些向量，JF1 保持兼容，新增协议能力使用并存的 JF2。向量通过仅代表软件一致，未经硬件验证。

## 帧格式

- ASCII 编码，区分大小写，首个 token 固定为版本 `JF1`。
- 每帧以 LF（`\n`）结束；接收端也接受 CRLF（`\r\n`）。Host 发出的请求固定使用 LF。
- token 之间只能有一个 ASCII 空格；不允许首尾空格、空行、TAB、内嵌换行或其他控制字符。
- 每帧上限 512 字节，包含行尾。串口读取最多取 513 字节，用于识别过长应答。
- 数值使用无符号十进制，0 写作 `0`，非零数不带前导零；不接受正负号、小数或指数。
- Clip 为 0–15；R、G、B 各为 0–255。每个 Clip 对应一颗 LED。协议颜色顺序是 **RGB**，灯珠实际驱动顺序由后续固件负责。

下表每行都省略结尾的 `\n`：

| 请求 | 成功应答 | 含义 |
| --- | --- | --- |
| `JF1 SET <clip> <r> <g> <b>` | `JF1 OK SET <clip> <r> <g> <b>` | 设置一个 Clip 的 RGB；ACK 原样回显数值 |
| `JF1 CLR <clip>` | `JF1 OK CLR <clip>` | 将该 Clip 清为 0,0,0 |
| `JF1 CLRALL` | `JF1 OK CLRALL` | 将全部 16 个 Clip 清为 0,0,0 |
| `JF1 STATUS` | `JF1 OK STATUS <r0> <g0> <b0> ... <r15> <g15> <b15>` | 恰好 48 个通道值，按 Clip 编号递增 |

`...` 只用于文档表示中间的通道，不是线上 token。示例：

```text
Host → JF1 SET 15 255 0 128\n
F103 → JF1 OK SET 15 255 0 128\n
Host → JF1 CLR 15\n
F103 → JF1 OK CLR 15\n
Host → JF1 CLRALL\n
F103 → JF1 OK CLRALL\n
```

## 错误应答

```text
JF1 ERR <code> <message>\n
```

`code` 满足 `[A-Z][A-Z0-9_]*`。`message` 为非空、可打印 ASCII 文本，词间单空格，不含控制字符。例：`JF1 ERR BUSY LED driver busy\n`。Host 将其映射为 `HostError("DEVICE_ERROR", ...)`，保留原文并将设备码放入 `details.device_code`。设备错误码是设备侧标识，区别于 Host 本地的错误码。

Mock 的无效请求会返回 `CLIP_OUT_OF_RANGE`、`INVALID_RGB`、`INVALID_COMMAND` 或 `MALFORMED_FRAME`。这些用于模拟拒绝行为，不表示已经存在对应的 F103 固件。

## 请求与应答配对

同一条串口连接一次只允许一个未完成请求，不支持流水线、异步事件或启动 banner 混入应答。设备应在完成请求后发送且只发送一条应答。Host 严格检查成功应答的命令及参数；`STATUS` 检查恰好 16 组合法 RGB。格式正确但命令/回显不一致会报 `UNEXPECTED_RESPONSE`；帧格式、数量、版本或范围不合法报 `MALFORMED_RESPONSE`。

空读取为 `TIMEOUT`；有字节但没有完整行尾视为不完整应答，报 `MALFORMED_RESPONSE`。传输失败后，Host 不再使用该会话，也不自动补写或重试。设备可能已执行请求，因此此时实际 RGB 状态未知。JF1 不含校验和或请求编号，不保证失败后的“恰好执行一次”；恢复前须按后续固件约定排除迟到应答并重新同步。

`STATUS` 表示设备报告的 RGB 值，ACK 表示协议处理成功，都不是 LED 光学反馈或电气测量结果。后续固件需保持请求校验失败时不修改 RGB 状态。

## Transport 约定

`jumperfree.host.Transport` 定义三个同步方法：

| 方法 | 约定 |
| --- | --- |
| `write(data: bytes) -> int` | 返回已写字节数；短写使 Host 事务失败 |
| `readline() -> bytes` | 在有限超时内返回一帧/部分帧，超时无数据返回 `b""`；实现方负责读取长度上限 |
| `close() -> None` | 释放资源 |

I/O 故障通过 `OSError` 报告，HostSession 转为结构化 `TRANSPORT_ERROR`。自定义 Transport 必须提供有限读写超时，HostSession 本身不另开计时线程。

`MockTransport()` 默认模拟 16 个全黑 Clip，支持分段写入和按行排队应答。`MockTransport(responses=[...])` 禁用自动模拟，仅按序返回指定 bytes，用于测试坏帧、设备 ERR 或迟到应答；显式传入空列表可模拟无应答。Mock 的空读取立即返回，不模拟真实耗时。该模型不含开关、拓扑或固件驱动。

## JF2

独立前缀的事务、租约、故障与 MockDevice 规范见 [JF2 主机与内存设备](../../protocol/jf2.md)。JF1 字节和 HostSession 保持冻结；当前全部为软件验证，未经硬件验证。
