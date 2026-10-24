# 16 节点交叉开关路由

路由层在显式的交叉开关资源图上工作：外部 Clip 节点、芯片 X/Y 线和芯片间固定链路。当前
产品拓扑由 `v01_topology()` 提供，见 [V0.1 软件拓扑](v01-topology.md)。它是 16 个 Clip、
两颗 CH446Q 风格芯片和 8 条共享 Y 通道的逻辑模型；拓扑、接线与时序均未经硬件验证。

## 建网流程

建网从字典序最小的成员节点开始，反复用确定性的最短路把最近的未连接成员接到已有树。固定边
代价为 0，每个需要闭合的交叉点代价为 1。芯片线在 net 间独占，固定连接成一组的线会一起保留；
成功路由还必须通过 `find_shorts` 检查，确保不同 net 没有意外短接。

调用者必须在请求中列出同一 attachment line 或同一固定接线连通分量上的全部节点。遗漏时原子地
返回 `NODES_PERMANENTLY_JOINED`，并在 `missing` 中列出缺少的节点。路由过程中若将未列出节点的
attachment line 纳入 net，也会以同样方式拒绝创建。

这是贪心 Steiner 近似，不保证最少交叉点或最大总体可路由率。除非拓扑连通性等不变量能够直接
证明不可达，`NO_FREE_PATH` 只表示当前空闲资源下未找到方案，不表示物理上不可能。

## 状态与隔离

状态文件保存拓扑、每个 net 的成员节点、独占芯片线和闭合交叉点。创建与删除都是原子的：失败
不修改输入状态，删除会一次释放该 net 的全部资源。稳定错误码包括 `NET_ALREADY_EXISTS`、
`NET_TOO_SMALL`、`NODE_NOT_IN_TOPOLOGY`、`NODES_ALREADY_IN_OTHER_NET`、
`NODES_PERMANENTLY_JOINED`、`NO_FREE_PATH` 和 `NET_NOT_FOUND`。

路由层只处理软件资源，不包含 CH446Q 寄存器、串口通信、固件或 PCB 信息。设备通信见
[协议目录](../../protocol/)，板级计划见 [板级计划](../arch/board.md)。
