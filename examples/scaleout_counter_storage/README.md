# 交换机计数的精确存储选项

原生Scale-Out的协议JSON可增加 `"switch_counter_storage": "port_dense"`，按每台交换机实际设备槽数（物理端口加slot0）保存入口×出口×8优先级的32位计数。默认/显式 `legacy_dense` 均保留1025槽，矩阵载荷33,620,000B/台。两种方式保留相同计数、包、排队、反馈和恢复；没有减少网络事件或提高模拟带宽。UB入口不使用这个参数。

存储在全部接口创建后、流量开始前分配；紧凑模式不会先分配完整矩阵再缩减。原生最多1024物理端口、原有计数检查和进程预算均保留。非Fabric的原生上游调用在首次访问时默认初始化1025槽。必须按当前锁文件重建；旧二进制不支持这个选项，不能仅修改JSON就声称已生效。

复用现有公开入口，输出目录必须不存在：

```bash
PYTHONPATH=python python3 -B examples/scaleout_counter_storage/prepare.py --output runs/counter-inputs
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout topology \
  --config runs/counter-inputs/small-topology.json --output runs/counter-topology
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology runs/counter-topology --protocol runs/counter-inputs/small-port_dense.json \
  --config runs/counter-inputs/small-work.json \
  --backend build/native-streams/output/moe-rdma/ns3.42-moe-rdma-stream-probe \
  --output runs/counter-result
```

原生构建用法和可选现有工具路径见[持久流示例](../scaleout_rdma_streams/README.md#执行)，实际二进制以构建的`backend.json.stream_probe`为准。路径相对当前工作目录解析；准备器默认从自己的源码位置定位仓库，可用`--repo`显式指定。准备器也生成故障和1024短任务的输入，不自动执行或提高预算。

显式选择模式时，`result.json.switch_counter_storage`给出模式、总矩阵载荷字节，以及各交换机实际/存储槽和非零计数。缺省不会新增该字段。优先级0沿用上游只加不减的累计行为；不要把它解读成仍在队列里的字节。字段范围见[输出说明](../../docs/record_storage.md)。

这项改进服务于降低网络仿真的宿主内存，不代表真实MoE任务更快，也不改变完整模型千卡超时结论。`scaleout capacity`和拓扑审计的`fixed_switch_counter_array_bytes`继续给默认legacy的核算；port模式的矩阵载荷可独立按 `sum((degree+1)^2*8*4)`复算。所有这些值都不包括MMU、路由、QP、包、JSON和采集，完整RSS仍须实际监控。
