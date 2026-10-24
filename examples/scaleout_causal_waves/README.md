# 保存的前端图接入持久网络

`rdma-causal-plan`将已有`moe-scaleout-wave/1`文件转换为持久字节入口的任务图，以便使用选择重传等参考协议执行计算与通信之间的依赖。原前端在线服务把图保存在`graphs/<graph_id>/wave.json`。本入口读取保存的图，不重新执行LLMServingSim调度或ASTRA，也不实现现代DeepEP/GIN。

本目录提供4端点、两组EP2、两步的人工功能输入。`receive_gate`是故意声明的本地等待，用于检查接收先到但本地前驱未完成的情况，不是测量数据或典型专家计算。

## 执行

在仓库根目录使用已有原生`stream_probe`程序。`runs`父目录须存在，各输出目录须不存在：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_causal_waves/topology.json --output runs/causal-topology
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-causal-plan \
  --config examples/scaleout_causal_waves/plan.json --topology runs/causal-topology \
  --protocol examples/scaleout_causal_waves/rdma.json --output runs/causal-plan
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology runs/causal-topology --protocol examples/scaleout_causal_waves/rdma.json \
  --config runs/causal-plan/work.json --backend /absolute/path/to/stream-probe \
  --output runs/causal-result
```

程序位置以已有`rdma-build`的`backend.json.stream_probe`为准。构建方式见[持久传输指南](../scaleout_rdma_streams/README.md)，不需要安装新依赖。`work.json`是普通v4输入，`mapping.json`保存原节点、消息完成关系、组完成与输入hash，供结果复核使用。hash校验内容一致，不认证数据来源。

## 更换输入与协议

`plan.json`根格式为`moe-rdma-causal-waves/1`，包含：

| 字段 | 内容 |
|---|---|
| `waves` | 明确列出的wave文件；相对路径从配置所在目录解析。每组必须从step0连续，组之间rank不能重叠 |
| `streams` | 与普通持久入口相同的连接声明；每个实际使用的`src/dst/plane`恰好一个，明确NIC、线端端口及队列深度 |
| `limits` | v4的`max_streams/max_work/max_link_transmissions`，不自动放大 |
| 可选协议字段 | `transport_recovery`、`dcqcn_reference`、`source_rate_capture`，原样接受普通持久入口的相应检查 |

选择重传的参数和约束见[持久恢复](../scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)。把所需字段加到plan根对象，再重新转换；不要直接修改映射清单。保持相同wave、连接、字节、计算成本及故障输入后，才能比较两种恢复机制。不同队列深度、NIC或线端端口也可能改变发布、路径和拥塞，需要分别说明。

## 完成怎样推进

每个SEND只提交一次write，完成于真实累计ACK；RECV不发送包，不占计算槽，其完成同时需要实际连续字节接收和原图本地前驱。COMP保留原声明纳秒时长，在每rank一个串行槽上执行。后续通信由这些实际事件触发，没有固定绝对发送时刻。

同组下一步要等上一图所有rank、所有节点完成，包括没有被计算后继引用的SEND确认。用于汇总这一条件的零时长join只处理组结束，不添加层间或Dispatch/Combine屏障。不同组没有新增相互等待。

这是新的持久传输参考执行方式。它改变一次性QP及发送信用的模型；同纳秒的可执行节点顺序、计算资源和发送资源不保证等同ASTRA。原图没有的现代库屏障、自发GIN、GPU可见性、张量计算或调用成本也不会由转换自动补出。

输入累计≤16MiB、≤512图/65536前端节点，EP2—32；输出work和映射各≤8MiB，仍受v4的4096连接/65536工作及每依赖64项保护。全局依赖成环、SEND/RECV不匹配、组轮次缺失或连接不明确均在写输出前拒绝。没有截层或静默忽略任务。

[验收记录](../../fixtures/causal_waves/README.md)包括两组短例及保存的完整48层/两步图在4端点上的实际执行；后者不是原1024卡部署的完成证据。计算仍为声明值，模型GPU标签不代表校准。
