# Jumperless V5 节点计数口径（参考，未验证）

这里的“节点”指一个可以独立参与电气网络路由的端点，例如一整条面包板行、一个 Nano 排针、一条电源轨或一个特殊功能端点。面包板同一行上的多个物理插孔通常属于同一个电气节点，因此 **物理孔数不等于节点数**。

参考资料描述了 12 颗 CH446Q（A–L），每颗有 16 条 X 线、8 条 Y 线；A–H 主要服务面包板，I–L 服务特殊功能。面包板共有 60 行。路径可以经中间芯片多跳，资料给出的量级约为每条路径最多 4 次芯片连接、每个闭合交叉点约 45 Ω。

这些数据仅作为建模参考，尚未在 JumperFree 硬件上验证。特别是 `bbNodesToChip` 的逐行映射存在注释偏移及第 29/30 行落在 K/L 等需要复核的细节；本项目不会据此宣称一个“官方总节点数”。机器可读记录见 `jumperfree/routing/topologies/jumperless_v5_reference.json`，其中明确标记 `"verified": false`。

参考来源：JumperlessV5 README、`RP23V50firmware/src/MatrixState.cpp` 的 `bbNodesToChip`，以及 Jumperless 文档 glossary。
