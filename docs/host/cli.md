# Stage-1 Host 使用说明

Host 负责 `Agent → Python Host → USB-UART → 外接 F103 BluePill → Clip RGB LED` 的主机侧协议。Clip 编号固定为 **0–15**，每个 Clip 对应一颗 LED，颜色使用 RGB 三通道整数（0–255）。本实现没有经过硬件验证。

本功能仅控制 LED，不执行电气网络连接；不包含双 CH446Q / 8-Y 拓扑、CH446Q 开关驱动、PCB 或固件移植。现有 `jumperfree route` 命令保持原有行为。

## 无硬件试用

Python ≥ 3.11，在仓库根目录执行即可，无第三方依赖：

```bash
python -m jumperfree host set --clip 0 --rgb 255,128,0 --mock
python -m jumperfree host clear --clip 15 --mock
python -m jumperfree host clear-all --mock
python -m jumperfree host status --mock
```

安装本项目后，也可以直接使用 `jumperfree host ...`。每次 CLI 调用都会新建 Mock，LED 初始为全黑，不会跨进程保存状态。需要连续演示颜色变化时使用下方 Python API。

每个子命令都必须明确选择 `--mock` 或 `--port PORT`，两者不能同时使用。成功退出码为 0，Host 错误为 1，命令行语法错误由 argparse 返回 2。

`--baudrate` 和 `--timeout` 仅用于串口；与 `--mock` 同时显式指定任一选项（包括默认值）会返回 `INVALID_SERIAL_CONFIG`，退出码为 1。

输出包含一行摘要和一行键排序、无多余空格的 `HOST_JSON`：

```text
HOST set OK
HOST_JSON {"data":{"command":"SET","values":[0,255,128,0]},"ok":true,"operation":"set"}
```

`data.command` 为协议命令，`data.values` 为应答中的数值。`STATUS` 返回长度为 48 的数组，按 Clip0 的 R/G/B、Clip1 的 R/G/B，直到 Clip15 排列。失败时用 `error` 替代 `data`，含 `code`、`message` 和可选的 `details`。

CLI 在打开串口前校验 Clip/RGB 参数；若事务和关闭连接均失败，保留事务错误，并在 `details.close_error` 中附上关闭错误。

## 可选串口

只有使用串口时才需要可选依赖 `pyserial`；它不是项目的硬依赖。在仓库根目录使用 `pip install .[host]` 安装项目及串口依赖：

```bash
python -m pip install '.[host]'
python -m jumperfree host set --clip 3 --rgb 0,64,255 --port /dev/ttyUSB0
python -m jumperfree host status --port /dev/ttyUSB0 --baudrate 115200 --timeout 1
```

`/dev/ttyUSB0` 只是端口示例，Windows 可按实际枚举结果填写 `COM3` 等。`--baudrate` 默认 115200，`--timeout` 默认 1 秒，并同时用于读和写；超时必须是大于 0 且不超过 3600 秒的有限数值，否则在打开串口前返回 `INVALID_SERIAL_CONFIG`。串口打开、写入或读取时的 `OverflowError` 也会转换为 `INVALID_SERIAL_CONFIG`，CLI 输出结构化 `HOST_JSON` 错误。串口使用 pyserial 默认的 8N1、无流控参数，需与后续 F103 固件一致。这些是软件默认值，不代表已经确定或实测的板级参数。

F103 必须另行提供符合 [JF1 协议](protocol.md) 的固件，本仓库没有实现该固件。BluePill 具体型号、USB-UART 器件、UART 外设、TX/RX/GND 引脚、电平与供电连接、WS2812 数据引脚及 Clip 灯序映射均为 **TBD**。引脚表暂不指定；未确认接线前，上述命令仅作为接口示例。

## Python API

```python
from jumperfree.host import HostSession, MockTransport

transport = MockTransport()
session = HostSession(transport)
try:
    session.set(0, 255, 128, 0)
    session.set(15, 0, 64, 255)
    response = session.status()
    print(response.values[:3])  # (255, 128, 0)
    session.clear(0)
    session.clear_all()
finally:
    session.close()
```

真实串口可替换为 `HostSession(SerialTransport(port, baudrate=115200, timeout=1.0))`，其中 `SerialTransport` 从 `jumperfree.host` 导入。`HostSession` 接管 transport 的关闭责任；`close()` 幂等，关闭后不再发命令。会话是同步的，不支持多个线程共享，也不能与其他会话共享同一串口。

参数错误在写入前抛出 `HostError`，不会改变设备或使会话失效。超时、短写、I/O 错误、设备 ERR、坏应答或 ACK 不匹配都会使会话失效；随后调用返回 `SESSION_CLOSED`，必须关闭后重新建立通信。`close()` 只关闭连接，不清灯，清灯需要显式执行 `clear` 或 `clear-all`。

## 故障定位与 CI

| 错误码 | 含义 |
| --- | --- |
| `CLIP_OUT_OF_RANGE` | Clip 不是 0–15 的整数（API 不接受 bool） |
| `INVALID_RGB` | RGB 数量/格式不正确，或通道不是 0–255 的整数 |
| `INVALID_COMMAND` | 协议编码时命令或参数数量错误 |
| `INVALID_SERIAL_CONFIG` | 串口参数不合法 |
| `SERIAL_UNAVAILABLE` | 未安装可选 pyserial |
| `TRANSPORT_ERROR` | 打开、写、读或关闭失败 |
| `SHORT_WRITE` | 请求未完整写入，Host 不自动补写 |
| `TIMEOUT` | 未收到任何应答字节 |
| `MALFORMED_RESPONSE` | 应答未完整换行、版本/格式/数值非法或过长 |
| `UNEXPECTED_RESPONSE` | ACK 命令或回显参数不匹配 |
| `DEVICE_ERROR` | F103 返回 ERR；设备错误码在 `details.device_code` |
| `SESSION_CLOSED` | 会话已经关闭或发生过事务失败 |

超时和通信错误不代表命令未在设备执行；Host 不自动重试。JF1 没有请求编号，重新建立通信前应确认设备已停止发送旧应答并按固件约定重新同步；仅新建 Python 对象无法消除线路上的迟到数据。重新同步后可用 STATUS 查询设备报告的 RGB 状态，不能据此证明 LED 实际发光或硬件工作正常。

CI 使用 `MockTransport`，测试串口适配器时替换导入的 serial 模块，不需要 pyserial 或真实端口：

```bash
python -m unittest discover -s tests -t . -v
python scripts/coverage_report.py
```
