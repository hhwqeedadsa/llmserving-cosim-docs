# 经典乱序等待参考

此配置为有界选择重传增加可选的固定NACK等待。真实包级功能已验证：确认推进取消等待、重复反馈不续期、单包丢失和无反馈恢复、RTO抢占及源流隔离；v5事务probe也验证了实际字节与一次副作用。它是经典参考，不是新算法或性能收益结论。分阶段证据和适用边界见[补验记录](../../fixtures/nack_grace_edges/README.md)。

在工作配置的`transport_recovery.mode=selective_repeat/1`内显式加入：

```json
"reordering": {"mode": "nack_grace/1", "wait_ns": 16000, "max_events": 10000}
```

三个字段必须完整且无额外字段。`wait_ns`单位ns，范围1至`initial_rto_ns`且不超过1e9；`max_events`为1至100000的独立日志硬上限，不是重试上限。布尔数值、go_back_n模式及未知模式均拒绝。省略该对象保持既有立即NACK恢复路径。

源收到当前缺失前缀的第一个实际NACK后等待固定时长，重复同缺口NACK不续期；累计ACK推进取消旧等待。超时恢复继续独立工作，优先取消等待，同一前缀不会在RTO之后再次启动等待。到期仍有未确认已发字节时才调用原生恢复与选择确认跳过逻辑。这里没有真实接收方状态先知、路径先知或合成反馈。它是经典固定等待参考，不是RACK、IRN复现或硬件校准。

## 公开操作

先按[原生构建说明](../scaleout_rdma/README.md)构建包含当前源码的后端，将`COSIM_STREAM_PROBE`设置为其绝对路径。输出目录必须不存在，父卷预留8GiB。本例为8个端点标签、10个交换机、单plane Clos，下层100G、上层25G，4us链路失效、16us全局更新，GPU类型NONE，没有执行GPU。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_nack_grace/topology.json --output /dev/shm/grace-topology
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /dev/shm/grace-topology --protocol examples/scaleout_nack_grace/protocol.json \
  --config examples/scaleout_nack_grace/work.json \
  --backend "$COSIM_STREAM_PROBE" --output /dev/shm/grace-result
```

这些公开命令已在新Python源码根和空cwd实际执行；native程序及固定库是外部依赖，不代表从零构建。该人工功能负载不能代表生产MoE或完整推理，不应把发送字节减少直接解释为完成时间改善。

结果新增`persistent_transport.nack_grace`，包含配置、连接最终状态和独立事件。`feedback_uid`只在实际反馈记录中存在；timer/关闭记录该值为-1，触发反馈保留在before状态，不能把它当重传数据包UID。字段见[输出说明](../../docs/record_storage.md)。v5事务probe已执行一次启用等待的真实丢包恢复，核5个远端操作/3次本地copy及原子返回值。启用等待后的在线事务session、PersistentStreams同ns真实包级竞争和全局stop组合没有新增执行证据；原生ns-3核心的7项事件顺序CPU核查不能替代这些集成覆盖。
