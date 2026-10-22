# 推理通信脆弱性的固定输入对照

本实验围绕三个问题：最终确认丢失如何使源额度和推理前驱停顿；自适应超时怎样改变故障代价；过低初值在短MoE通信中是否能自动纠正。不给“自适应总更好”设通过条件，不更改专家选择制造收益。

固定8GPU、两组交错EP4推理副本、batch/rank4、H1024/E16/Top2、1层2步ready-KV Decode、100G三层Clos；每源3消息/8192B FIFO软件额度，计算是声明值。无训练DP同步、真实专家热点或硬件RTO校准。目标从首个真实图选取0→4 Dispatch6144B，共同已提交图必须逐字节相同；所有副本/步骤共享同一个ns-3。

八个条件为固定/自适应超时各四组：无故障、最终ACK丢失一次、持续丢失至两次重试耗尽、无故障但初始RTO2μs。正常初值40μs、上限160μs，自适应下限1μs；短初值组上限64μs、最多8次重试。所有参数都显式声明。故障同一源/消息/字节/次数，不能把停止时间当完成时间。

区分解释的方法：

1. 若反馈缺失导致系统停顿，应观察到接收已齐、确认不足、额度未归还；恢复后须有真实反向ACK和清理，后续通信才能获准。
2. 若差异来自RTO学习，应能从源包/ACK独立重算样本、基准、到期和额外字节；若没有有效样本，固定/自适应应有相同实际执行，不能归给学习收益。
3. 持续丢失应用形式化的静默界解释何时失败，不能算成成功率提升。短超时即使最后完成，也必须报告多发流量和无效测量。
4. 同一单通信等待变化不能直接替代请求影响。核对两副本实际图、前端波次和资源释放；网络额外流量可能改变其他等待，固定DAG单节点扰动定理不能无条件套用。

```bash
PYTHONPATH=python python experiments/inference_fragility/run.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/inference-fragility
python experiments/inference_fragility/analyze.py \
  --input /dev/shm/inference-fragility --output /dev/shm/inference-fragility-analysis
```

当需要解释一次ACK变化为何与整体推进变化不相等时，对同一故障对照增加两次有界包观察，要求除新增包记录外整个结果逐字段相同，再运行带`--capture`的分析：

```bash
PYTHONPATH=python python experiments/inference_fragility/capture.py \
  --experiment /dev/shm/inference-fragility --backend-json "$COSIM_BACKEND_JSON" \
  --protoc "$COSIM_PROTOC" --output /dev/shm/inference-fragility-packets
python experiments/inference_fragility/analyze.py \
  --input /dev/shm/inference-fragility --capture /dev/shm/inference-fragility-packets \
  --output /dev/shm/inference-fragility-analysis-with-packets
```

包观察限100000条、5ms窗口；该补充阶段累计产物128MiB。没有改批次、故障或时序，不把观察运行算作额外性能样本。分析按实际数据包区分源端发出跨度、链路传播/序列化和交换机驻留时间，保留不能由队列解释的部分。

本目录维护独立输入，只调用公开CLI；不导入tests、功能fixture或历史测量程序。实际命令、版本/hash、输入、失败和资源保留在输出。单例60秒/RSS1GiB/地址2GiB/仿真5ms，累计产物256MiB、卷预留8GiB，失败不自动扩容或重试。分析是离线评价，不为在线恢复提供全局真值。

结果见[results.md](results.md)，条件证明见[推理通信脆弱性](../../docs/communication_fragility.md)，平台功能验收另见[自适应门](../../fixtures/scaleout_adaptive/README.md)。
