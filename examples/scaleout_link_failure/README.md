# 关闭真实交换机链路并观察重传与改路

本例用于检查 Scale-Out 网络中链路永久失效后的包、排队、缓冲、流控和完成事件。8个GPU标签、10个交换机构成一个plane的Clos；消息是人工字节负载，不是完整MoE推理。关闭一条真实交换机间边的两个端口，保留已经发出的在途包，在声明延迟之后集中更新最短路径与等价多路径（ECMP）表。源连接保留字节序号和拥塞状态，由实际ACK、NACK及可选超时重传继续推进。

## 运行

使用锁文件固定依赖及[原生RDMA构建](../scaleout_rdma/README.md)，新`rdma-build`的`backend.json.probe`提供可执行路径。`$COSIM_PROBE`和`$COSIM_STREAM_PROBE`分别替换为实际probe、stream_probe路径，不需要GPU或新安装。以下目录须不存在，父目录已存在，卷预留8GiB：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_link_failure/queued-topology.json \
  --output /dev/shm/link-topology
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/link-topology \
  --config examples/scaleout_link_failure/queued-rdma.json \
  --messages examples/scaleout_link_failure/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/link-result
PYTHONPATH=python python3 fixtures/switch_link_failure/audit.py \
  --topology /dev/shm/link-topology/topology.json \
  --config examples/scaleout_link_failure/queued-rdma.json \
  --messages examples/scaleout_link_failure/messages.json \
  --result /dev/shm/link-result/result.json --output /dev/shm/link-audit.json
```

主例有三条64,000B消息，下层100Gb/s、上层25Gb/s，4,000ns关闭8—10，16,000ns更新路由。它刻意形成故障时的排队，用于功能核验，不是优化算法或生产故障参数。`topology.json`配合`rdma.json`提供上层同为100Gb/s的版本；`baseline-rdma.json`省略故障，可在各自同一拓扑上作无故障对照。不能把不同拓扑的时延直接当作故障开销。

## 可配置范围

协议v1/v2均可增加：

```json
"link_failures": {
  "mode": "switch_links/1",
  "events": [
    {"src": 8, "dst": 10, "time_ns": 4000, "route_update_delay_ns": 12000}
  ]
}
```

`src/dst`必须为生成拓扑中同plane的一条实际交换机边，`src<dst`；最多8条不同边。时刻和延迟是非负整数，关闭与路由更新时间必须严格早于`max_sim_ns`，0延迟合法。整个列表在分配网络前检查。改变拓扑之后必须重新选择边ID；接口拒绝GPU/NIC边、重复关闭、额外字段和越界值。

每条故障的路由延迟独立计算。更新只排除已经到达各自更新时间的边；其他物理失效尚未被路由获知，旧表仍可能选到死出口并真实丢包。路由通过实际IP/端口的原生哈希选择下一跳，连接和消息ID不替代哈希字段。排队包不因改路而迁移到另一出口。

永久失联的流可能重试耗尽并失败，配置故障不保证完成。旧字节入口在协议配置`recovery`；持久字节/事务入口在工作配置`transport_recovery`。两者不能混用。不开启恢复时仍可执行物理故障，但丢失尾包或最终ACK可能使工作无法结束。

## 其他功能条件

均使用`queued-topology.json`生成的同一物理拓扑，新输出目录须分别命名：

| 条件 | 协议 / 工作输入 | 目的与预期 |
|---|---|---|
| 不同检测延迟 | `two-failures-rdma.json` / `messages.json`，rdma-probe | 8—10和9—11先后失效，分别16,000/125,000ns更新，三流完成 |
| 流控与headroom | `pfc-rdma.json` / `pfc-messages.json`，rdma-probe | 合法1MiB交换机缓冲、三流各256,000B，15,000ns故障；检查PFC暂停/恢复和缓冲回收 |
| 持久连接 | `stream-rdma.json` / `stream-work.json`，rdma-stream-probe | 两连接、四项因果工作；空闲后追加，保留同一个QP的序号和状态 |
| 未来故障 | `future-rdma.json` / `future-work.json`，rdma-stream-probe | 17B早已ACK，仍等待500,000ns故障和512,000ns改路再关闭 |
| 永久分区 | `partition-rdma.json` / `partition-work.json`，rdma-stream-probe | 断开ToR8的全部上联，预期命令失败；健康source2完成、source0保持未确认，保存部分结果 |
| 远端事务 | `stream-rdma.json` / `transaction-work.json`，rdma-transaction-probe | 实包携带put及fetch-add，检查重传下6操作应用和最终内存；不是生产RoCE原子协议 |

例如持久入口：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /dev/shm/link-topology \
  --protocol examples/scaleout_link_failure/stream-rdma.json \
  --config examples/scaleout_link_failure/stream-work.json \
  --backend "$COSIM_STREAM_PROBE" --output /dev/shm/link-stream-result
```

## 输出解释与边界

`result.json.link_failures`保存配置、关闭/改路事件、待处理事件数和路由代次。关闭记录包含两端端口、排队字节、关闭时本地丢包数及两交换机关闭前后缓冲摘要。入口、出口、共享占用是同一数据的不同视角，不能相加为物理内存。

完整包记录中`drop=2`表示物理接收丢弃，包括在途包到达已关闭端口；显式故障模式新增`local_drop=5`表示排队清空或旧路由无法转发。只有物理接收/丢弃结束一次物理在途传输，本地丢弃不能再次递减在途计数。控制优先级0不占数据MMU，正常dequeue中的ECN/PINT/发送统计不能用于排队丢弃。健康入口达到恢复条件时发送真实PFC恢复报文。

故障和路由事件未结束时，物理排空检查不会允许提前关闭。示例probe固定运行到预算时限，因此顶层`finish_ns`不是消息完成时延，应读各消息`ack_ns`。持久入口在全部工作及网络事件结束后停止。

本例v1不恢复链路；临时断链及恢复请使用[独立v2示例](../scaleout_link_restore/README.md)。目前没有NIC失效、rail重绑定、分布式控制报文、GPU内核终止或内存注册撤销。控制延迟是声明参数，没有硬件校准；PFC/字节/事务例只是功能核验，不能据此声称千卡、现代GIN、生产MoE或恢复性能收益。独立核验、失败和复现入口见[验收记录](../../fixtures/switch_link_failure/README.md)。
