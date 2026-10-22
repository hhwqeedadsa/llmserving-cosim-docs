# Scale-Out 丢包与包级诊断

本例保持默认原生协议；可选超时恢复另见[恢复小例](../scaleout_rdma_recovery/README.md)。本例使用独立的原生 RDMA ns-3，覆盖中间数据包丢失恢复和最终 ACK 丢失停滞。所有丢包均发生在指定设备的接收错误模型中，在 `MacRx` 和协议处理之前；不修改路由、速率或重传实现。不需要模型下载或历史运行。

先按[RDMA 小例](../scaleout_rdma/README.md)构建后端，并从新构建目录的`backend.json`读取`probe`、`service`。不要使用包含 UB 的`cosim-service`。下面的`$COSIM_PROBE`、`$COSIM_SERVICE`应替换为实际路径；输出目录须不存在，运行卷至少预留8 GiB。共享内存目录是可选运行位置，退出机器后应另存必要结果。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/fault-topology
python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/fault-topology --config examples/scaleout_rdma_faults/middle.json \
  --messages examples/scaleout_rdma_faults/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/fault-middle
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/fault-middle --output /dev/shm/fault-middle-diagnosis.json
```

将`middle.json`换为`final-ack.json`并换新输出目录，探针预期非零退出。失败目录仍有`result.json`、输入hash和资源监测记录，可用同一`rdma-diagnose`读取；不要把命令非零退出改写成运行成功。正常恢复由`rdma-probe`自行检查完成，真实服务成功后还会核验业务字节、依赖及前端推进。

真实服务故障入口：

```bash
python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_rdma/service.json --topology /dev/shm/fault-topology \
  --protocol examples/scaleout_rdma_faults/service-final-ack.json \
  --backend "$COSIM_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/fault-service
# 上一命令预期失败；随后单独执行诊断，不用 && 隐藏失败产物。
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/fault-service --output /dev/shm/fault-service-diagnosis.json
```

此配置对应原小例第一轮消息0（GPU 0→1，2048 B）的最终ACK。更改模型、batch、组或路由会改变消息；应先做无故障运行，从实际`graphs/*/wave.json`选取目标，不能沿用不再存在的消息。服务故障实验使用有限 ready-KV Decode 与声明计算成本，不是完整硬件故障恢复验证。

## 可以修改什么

`faults`最多32条，所有字段必填；不能与旧`drop_once`同时配置。

| 字段 | 含义 |
|---|---|
| `node`、`port` | 接收设备的节点与本地端口；`port=0`匹配该节点全部端口。支持GPU或交换机，用拓扑JSON核对编号。 |
| `message_id` | 当前执行的逻辑消息身份，按原消息端点及端口关联；支持大于49999的身份，不只比较取模后的端口。 |
| `kind` | `data`、`ack`或`nack`。ACK/NACK沿反向路径，接收节点通常是原发送GPU。 |
| `sequence` | 数据包起始字节偏移，须按payload对齐；ACK/NACK为累计确认或期望的下一字节，不是包编号。 |
| `start_ns`、`end_ns` | 绝对仿真纳秒的左闭右开窗口，必须落在仿真预算内。 |
| `count` | 最多丢弃多少次匹配；所有rail/设备共享计数，含重传。重叠规则由第一条匹配规则计数。 |

规则没有命中也会导出`drop_counts=0`；诊断列出`unmatched_fault_rules`。零命中不能算故障实验成功。此功能是确定性包丢弃，不是链路Down/Up、交换机失效、随机误码或动态重路由模型。

`packet_capture`默认不存在，即关闭。开启时声明`nodes`（空数组表示所有节点）、纳秒窗口与`max_events`（1..100000）。事件包含发送、接收、接收错误丢弃及BEgressQueue入/出队，记录逻辑消息、字节序号、协议、包谱系UID、整包/有效字节、ECN和该端口出口队列字节。ACK高优先队列和NIC数据QP不属于BEgressQueue，不能据此测出它们的排队时间。PFC/CNP包可以记录，但不强行映射到业务消息。

到条数上限后停止保存详细事件，继续仿真并累计遗漏数；`complete=false`明确表示所选范围内的记录不全，不影响传输。完整性仅针对所选节点和时间范围；关闭、筛选或截断时，未看到事件不能证明它没发生。

`egress_queue_bytes`是钩子当时的原生读数：入队事件在加字节之后，出队事件在减字节之前，因此出队读数仍含正在选出的包。原有`queue_events`数值保持原行为，本轮更正`queue_scope`的旧描述；不能把相邻读数都当作出队后状态积分。TX/RX事件处读的是该设备当前出口队列，不代表这个接收包的入队等待。

## 结果怎么看

- `source_data_payload_bytes`含重传；`source_unique_data_payload_bytes`为源端连续发送覆盖的唯一业务字节；`receiver_contiguous_bytes`为接收协议连续接受的字节。功能核验分别检查，不能强制重传后的总字节等于业务量。
- `receive_ready_ns`放行接收消费者；`ack_ns`结束发送。最终ACK丢失可以出现前者存在、后者为空。
- `first_packet_path`只记录第一个数据包尝试的路径；该尝试在交换机被丢弃时路径可以提前结束。重传及控制包路径用包事件查看，不把多个尝试串成一条假路径。
- `rdma-diagnose`是离线评价接口，读取完成或失败结果，报告未完成通信、故障命中、实际任务节点进度和直接依赖。其命令成功仅表示诊断生成成功；原仿真是否通过见`run_passed`。

所有跨节点身份、全局时间和依赖关联均是内部评价资料，不作为未来准入策略的可读输入。尾包或最终ACK丢失时，当前锁定库可能停滞；这描述的是本仿真后端，不代表生产RDMA没有恢复机制。
