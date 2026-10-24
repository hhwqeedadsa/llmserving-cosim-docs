# 查看交换机队列与缓冲计数是否一致

本例检查 Scale-Out 网络结束或预算截止时的交换机状态，便于排查“队列已空、缓冲仍占用”。只读取原生状态，不释放占用、不改变传输或故障处理。它不提供链路 Down/Up 或动态重路由。

先按[原生 RDMA 小例](../scaleout_rdma/README.md)构建，使用新构建 `backend.json` 中的 `probe`。已有 Python 环境需设置 `PYTHONPATH=python`；以下输出路径须不存在、父目录已存在、卷有8GiB预留。`$COSIM_PROBE`替换为实际可执行路径。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/mmu-topology
python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/mmu-topology --config examples/scaleout_rdma_mmu/rdma.json \
  --messages examples/scaleout_rdma_mmu/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/mmu-run
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/mmu-run --output /dev/shm/mmu-diagnosis.json
```

消息是三个源各向GPU4发送64,000B的网络功能负载，不是完整MoE或真实推理。正常例应完成并排空。协议中的可选参数为：

```json
"mmu_snapshot": {"nodes": [8,9,10,11,12,13,14,15,16,17], "max_ports": 128}
```

`nodes`显式选择1—16个不同交换机。`max_ports`为全部所选交换机的物理端口总预算，范围1—1024；每个所选交换机的所有端口必须纳入。拓扑改变后须重新核对ID和端口总数；GPU、重复节点或预算不足会在启动前拒绝，不截断快照。省略整个`mmu_snapshot`即关闭，结果不增加该字段。示例同时开启包采集供独立复核；实际使用快照不要求开启包采集或队列事件明细。

`result.json.mmu_snapshot`保存原生Report时刻、所选范围、每交换机reserve和共享占用，以及各端口8个优先级的入口字节、headroom字节、出口MMU字节、实际队列字节、PFC暂停状态和link状态。`rdma-diagnose.switch_buffer_consistency`报告q1—7的守恒及具体不一致位置。

控制队列0不参加MMU守恒；队列也不包括正在序列化和链路在途的包。入口、出口和共享计数是同一数据的重叠视角，不能相加为物理内存。一致只说明所选范围的该时刻，不证明历史、物理排空、恢复安全或硬件准确；诊断不改写原仿真passed。

## 查看尚未排空的真实队列

`queued-topology.json`把上层带宽降到25Gb/s，`queued-rdma.json`明确限定4,000ns。它是查看部分状态的小型功能例，预期预算截止、命令非零退出，不能报告为仿真完成：

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma_mmu/queued-topology.json --output /dev/shm/mmu-queued-topology
python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/mmu-queued-topology --config examples/scaleout_rdma_mmu/queued-rdma.json \
  --messages examples/scaleout_rdma_mmu/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/mmu-queued
# 上一命令预期失败；单独运行诊断，保留实际失败结果。
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/mmu-queued --output /dev/shm/mmu-queued-diagnosis.json
```

参考验收在截止时有89,096B数据仍排队，但队列与MMU一致。未完成与计数错误是不同结论。详细验收和独立队列事件核对见[功能证据](../../fixtures/mmu_snapshot/README.md)。
