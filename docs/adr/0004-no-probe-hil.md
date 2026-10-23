# 0004 不预留探针/ADC，SELFTEST 2 返回 UNSUPPORTED，自动化 HIL 不在范围（D5）

## 状态

已采纳用户对 D5 的否决，覆盖原方案中 Tester 探针、ADC 与自动化 HIL 建议。

## 背景

原方案的连通探测和自动化 HIL 依赖额外 Tester 硬件。用户明确将其排除出 V0.1。

## 决定

V0.1 板不预留 Tester 探针或 ADC，不做 SELFTEST 2 连通探测；后续 JF2 对 `SELFTEST 2` 返回 `UNSUPPORTED`，不宣称已有该能力。自动化 HIL（原 M10）不在范围，物理通断与隔离通过人工万用表测量并记录证据。软件 Mock、C 单测和 FIL 保留。

## 后果

设备影子只代表软件认知，不能验证外接电路或实际开关。不得宣称自动连通检测或过流保护。M1 全部成果未经硬件验证；后续 verified 仍需人工证据 PR，不由工具自动翻转。
