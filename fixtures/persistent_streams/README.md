# 持久原生字节传输验收

公开操作见[示例](../../examples/scaleout_rdma_streams/README.md)。这里实际运行锁定原生RDMA ns-3，不用人工完成代替网络。输入为人工功能场景，不是生产MoE；没有GIN原子事务、GPU可见或硬件校准证据。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 timeout 60s python3 -m unittest discover \
  -s tests/unit -p '*streams_test.py' -v
PYTHONDONTWRITEBYTECODE=1 timeout 300s python3 tests/integration/check_persistent_stream_workflow.py \
  --backend /absolute/path/to/ns3.42-moe-rdma-stream-probe --output runs/my-stream-check
```

检查从仓库根运行，在新cwd执行7条公共CLI：拓扑生成、深度1、深度2、同连接两条同时就绪工作、重复、未来依赖/错误NIC两类拒绝。每次网络运行≤60秒/RSS1GiB/地址2GiB，合计≤300秒/新增产物32MiB，卷预留8GiB。命令、退出码、stdout/stderr与失败均保留，见[验收摘要](verification.json)和[维护记录](maintenance_checks.json)。

| 核查 | 证据与范围 |
|---|---|
| 真实共享网络 | 两连接、6工作、31,457B、35个实际源数据包；native动态库来自同一目录，无UB |
| 因果就绪 | ready等于accepted/acked前驱的实际回调最大时刻，根工作为0；无预设后续时刻 |
| 队列反压 | 深度1时work1等ACK才发布；深度2时可在接收回调立即发布，仍不提前归还旧槽 |
| 持久序号与边界 | 独立按bytes/MTU枚举全部35包，核对累计序号、源NIC、有效负载、无跨工作包或提前发包 |
| 空闲后追加 | 一个stream只创建一个QP；空闲后只恢复调度器成员关系，序号不归零 |
| 同时发布 | 两工作在0ns进入同一深度2连接，保持逐工作包边界与ACK完成 |
| 完成与排空 | accepted早于ACK，在途帧/队列归0后close，拥塞状态只在close时终止 |
| 非法条件 | 前向依赖在运行前拒绝；NIC不匹配由native拒绝并保留child_failed日志 |
| 重复性 | 同配置两份result逐字节一致，不能据此推导硬件准确 |

适配器使用公开设备回调和RdmaHw方法，不改固定依赖。原生ReceiveAck会在当前size全部ACK后删除QP，独立路径改为推进实际ACK前缀、处理正常NACK/DCQCN并保留连接；普通消息入口不变。原生调度器会移出空闲QP，适配器只恢复成员，不调用重置nextAvail的NewQp。包由原生GetNxtPacket生成，临时将MTU限制在已发布工作边界后立即恢复。

这些选择不宣称逐条等价NCCL GDAKI。信用按ACK释放，未模拟真实CQE压缩或早期源buffer复用；字节流没有远端窗口、原子响应、重传恰好应用一次或GPU内存序。本入口尚未接现代事件核心/LLMServingSim，不能将35包称为现代DeepEP包。
