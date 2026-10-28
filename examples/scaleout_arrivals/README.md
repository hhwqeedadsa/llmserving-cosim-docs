# 任务两端统计与collective累计有效到达

`scaleout rdma-arrivals`读取已完成或失败的`rdma-run`目录，按实际图、图内层、Dispatch/Combine选择一个通信切片。输出为本次仿真的评价记录，不是实际RNIC/GPU测量。

运行服务时在RDMA协议JSON增加：

```json
"packet_capture": {"nodes": [], "start_ns": 0, "end_ns": 5000000, "max_events": 100000}
```

捕获终点必须晚于实际仿真结束时间；`nodes=[]`表示全部节点。采集满而截断时命令会拒绝恢复完整曲线。然后执行：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-arrivals \
  --run /path/to/saved-service --graph g0.w0 --layer 0 --phase dispatch \
  --output /path/to/new-arrival-audit
```

`g0.w0`是第0个EP组的第0轮，`--layer`是图内层号，真实原层号另存`source_layer_id`。省略`--src/--dst`得到整个阶段，增加它们可选接收rank或一对rank。此处collective指直接成对All-to-All-v阶段，不推断真实NCCL/DeepEP channel/chunk身份。输出目录必须不存在。

| 文件 | 内容 |
|---|---|
| `summary.json` | 任务生成/已提交消息数、逻辑字节、实际发送/接收载荷及切片完成边界 |
| `endpoints.csv` | 每rank的逻辑发送/接收、实际载荷、有效接受字节；数据和控制包的双向包数及模型包字节 |
| `messages.csv` | 所有已生成图的逐消息源目的、层/阶段、包数、重复发送、乱序丢弃与完成时刻 |
| `packet_events.csv` | 所选切片逐包逐跳发送/接收/丢弃/排队，时间、端口、序号、UID、接受增量，保留原捕获顺序 |
| `arrival_curve.csv` | 源发送载荷、物理接收载荷、去重物理到达、协议连续接受、源端反馈确认五条曲线 |

接受规则来自锁定Go-Back-N接收端：`sequence == next_expected`推进前缀；旧序号是重复，较大序号为乱序丢弃。MacRx早于协议接受，不能直接把RX字节相加作为有效数据。逐消息最终曲线须吻合`receiver_contiguous_bytes`，完成时刻须吻合`receive_ready_ns`，源包数/字节须吻合原生源计数。UID是数据包血缘，同UID可能多次发送，不能按UID去重。

当前支持完整捕获、纯包网络的`rdma-run`原生有序接收，最多64MiB result、64MiB图、1024图、100000事件。机内crossbar、持久stream选择重传和RC事务使用其他记录语义，当前拒绝。失败时只统计已生成图，不补未来轮次；协议接受不等于GPU可见。

`declared-119b.json`为百B级GQA MoE结构声明：48层、H4096、专家FFN1536、E128/Top8、EP8，每rank每层16个专家。它是可修改的结构例子，路由和计算为合成/声明值，没有真实模型权重或GPU profile。运行须匹配8GPU拓扑与协议。

该声明共119,051,534,336参数，EP8的BF16权重为35,165,863,936B/rank（约32.75GiB），默认上下文的KV预留约0.378GiB/rank；容量不含激活、workspace和分配器开销。参数量不会全部变成每次推理通信字节：本入口传输激活，流量主要取决于token数、隐藏维、Top-K、层数及专家放置，不能按模型参数量同比放大。

服务模型当前限制E=2—256、Top-K≤8、每rank batch=1—16；`service/1`为1—4层MoE片段，`service/2`为1—128层全MoE GQA声明。真实回放须保留原模型E/K，不能把R1的256专家改成128再沿用原选择；可调整合法EP及等容量专家放置。E256的EP4/8/16分别对应每rank每层64/32/16个专家。全任务逐包导出还受采集事件上限约束，百B形状通过容量核算不等于已完成百B全模型的逐包执行。

真实路由复杂场景和绘图见[实验说明](../../experiments/collective_arrivals/README.md)。
