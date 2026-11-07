<div align="center">

# JumperFree

### 给 AI Agent 用的面包板

**Agent in the Hardware Loop**

<sub>让 Agent 规划接线 · 让 LED 指引你插件 · 让测量确认接对 · 然后才烧录</sub>

</div>

<br>

## 为什么做

Agent 已经能写出嵌入式代码，却碰不到实体电路。

引脚要人查，线要人插，接错了要人用万用表找，烧录和排错也要人盯着。在硬件开发的循环里，人成了 Agent 的手和眼睛。

JumperFree 把 Agent 放进这个循环：

1. **Agent 规划**：决定器件插在哪里。
2. **LED 指引**：板上的 LED 告诉你每个器件插到哪个位置。
3. **测量检查**：插好后，Agent 用检查接线。发现错误，就告诉你怎么改。
4. **烧录与回读**：检查通过后才烧录，再实时修改烧录程序。

> **Agent 负责理解和规划，确定性软件负责计算连接，硬件负责安全执行。**



## 致谢

感谢 [JumperlessV5](https://github.com/Architeuthis-Flux/JumperlessV5) 提供的卓越灵感。
