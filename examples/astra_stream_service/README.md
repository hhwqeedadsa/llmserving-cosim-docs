# ASTRA服务使用持久连接与选择重传

本例仍由实际LLMServingSim前端选择批次、ASTRA执行任务图，仅将SEND下面的传输切换为持久连接。RECV只等待实际接收，发送资源等实际ACK再释放。所有组和轮次共用一个原生ns-3网络；不把保存的DAG交给另一个执行器。

输入沿用四份人工路由，四卡/EP4、每卡一个请求、两层两步Decode，初始KV已就绪。计算为声明成本，GPU标签不提供硬件校准。**这是一组功能小例，不是R1实测流量或新算法收益实验。**

## 运行

使用已有前端环境、protoc及当前非UB服务可执行文件。首次构建见[原生RDMA说明](../scaleout_rdma/README.md)；构建收据必须与实际二进制匹配。下面输出路径须不存在，先建立父目录，并保留至少8GiB空间；协议中已声明单例60秒、RSS1GiB、地址2GiB、模拟5ms和产物额度。

```bash
PYTHONPATH=python python -m moe_ub_cosim scaleout topology \
  --config examples/astra_stream_service/topology.json --output /path/to/new/topology

PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-run \
  --config examples/astra_stream_service/service.json \
  --topology /path/to/new/topology --protocol examples/astra_stream_service/rdma.json \
  --backend /path/from/matching/backend-json/service --protoc /path/to/protoc \
  --output /path/to/new/service

PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-check --run /path/to/new/service

PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-arrivals \
  --run /path/to/new/service --graph g0.w0 --layer 0 --phase dispatch \
  --output /path/to/new/arrivals
```

`service.json`没有`transport`时仍用旧逐消息路径；选择持久路径须显式填写整个`transport`。SimAI服务将同一字段放在嵌套的`service.transport`，沿用`simai-run/simai-check`，其稀疏来源约束仍独立生效。

## 配置和支持边界

| 字段 | 内容 |
|---|---|
| `schema_version` | `moe-astra-message-transport/1` |
| `streams` | 明确`id/src/dst/plane/wire_port/source_nic/destination_nic/queue_depth`；每个有流量的`(src,dst,plane)`必须有唯一绑定 |
| `limits` | 显式`max_streams/max_work/max_link_transmissions`；分别最多4096/65536/2000000 |
| `transport_recovery` | `go_back_n/1`或`selective_repeat/1`，继承[持久流恢复](../scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)；服务目前只允许耗尽后停止整个运行 |
| `dcqcn_reference` | 显式的DCQCN参考模型配置；SR必须使用此参考模型，不能等同所有生产RoCE实现 |
| `source_rate_capture` | 可选有界源速率记录，继承持久流输入格式 |

每消息最多1MiB、每连接累计最多64MiB，不自动拆分。实际生成消息提交前再次检查累计预算；合法batch仍可能因热点聚集造成单消息超限。只支持显式物理RDMA路径，不支持机内crossbar旁路、旧`admission/recovery/lifecycle_capture/drop_once`混用、流失败隔离或动态开关连接。不传实际数值payload，不模拟GPU可见性。

包捕获须覆盖0至`max_sim_ns`、所有节点且条数有界；截断不能通过完整核验。多rail由明确NIC绑定约束，但一组`(src,dst,plane)`只支持一条stream，不提供同plane逐消息动态选rail。

## 身份、输出和失败

语义消息有自己的`id/tag`，一个消息对应持久连接上一段实际发布的`[begin,end)`字节。**包记录及故障规则中的`message_id`在此模式下是stream ID。** 不要用语义消息ID注入故障，也不要把连接累计sequence当作每消息从零开始。

`result.json`的旧`messages`为空，新数据位于`service_transport.messages/native`；具体字段见[记录格式](../../docs/record_storage.md#astra服务持久传输记录)。公开检查从原件和声明重建图，核对实际ASTRA issue/callback、字节区间、有限乱序接受及源ACK，不使用未来时间驱动执行。

持久路径的到达摘要为`moe-rdma-arrival-audit/2`：曲线区分`unique_arrival_bytes/accepted_bytes/source_confirmed_bytes`；`packet_events.csv`保存完整抓包，`stream_id`保持原始身份，`semantic_message_ids`是评价层的关联。旧逐消息路径仍使用`/1`，不能不检查schema就混画。

`native.stdout.log`只保存DECIDE控制帧；原生诊断包括ASTRA异步日志转入`native.stderr.log`及原有日志文件。解析损坏/截断帧明确失败，不截取JSON前缀继续。成功业务、排空和失败停止时刻见公共终态字段；重试耗尽保留失败原件，非零退出不能单独证明预期恢复失败。

功能验收与边界见[固定验收摘要](../../fixtures/astra_stream_service/README.md)。现有SR、GBN、RTO与拥塞控制均为参考机制，不作为论文创新。
