# 0001 分层与依赖方向

## 状态

已采纳；audit / layering 已提供 Python 基础与 AST 门禁，完整架构逐步实现。

## 背景

路由意图、板级映射与设备 I/O 混合会使纯算法难以复用，也使硬件替换影响上层。

## 决定

采用 L4 service、L3 routing、L2 board、L1 device/host、L0 firmware。core 仅依赖标准库及自身；routing 与 host 仅依赖 core 和自身；board 依赖 core、routing；device 经 host 会话调用设备；service 组合意图、计划与设备。路由算法与板级编译保持纯、确定性。详见 [分层边界](../arch/layers.md)。

## 后果

通过 `tests/test_layering.py` 阻止反向导入；遗留 CLI I/O 边界按现状文档记录，不在 M1 重构。固件不做路由，硬件行为未经硬件验证。
