# 远端通信与同节点服务共用目标内存

这个例子检查远端数据到达后，同一节点的计数更新是否按序完成，以及后继网络发送是否读到更新后的真实字节。它用于补齐通信库中self-peer（自己作为通信对端）的功能语义；不估算真实网卡loopback、GPU可见性或GIN完成队列成本。

先生成8端点、2平面的拓扑，再用现有原生事务程序运行。`SELF_BACKEND`指向本轮构建的实际可执行文件或配套启动器；程序不自动编译或下载。所有输出目录须尚不存在。

```bash
export PYTHONPATH=python:.
export PYTHONDONTWRITEBYTECODE=1
SELF_BACKEND=/absolute/path/to/run-transaction-probe

python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_context_transactions/topology.json --output runs/self-topology
python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/self-topology --protocol examples/scaleout_self_peer/rdma.json \
  --config examples/scaleout_self_peer/program.json --backend "$SELF_BACKEND" --output runs/self-run
```

用`rdma-transaction-session`及对应的session程序可验证实际回调驱动前端；把协议换成`drop-first-rdma.json`可在远端首包真实丢失后检查恢复。功能验收与独立字节核查见[验收说明](../../fixtures/self_peer_service/README.md)。

输入限定`moe-rdma-transactions/5`，顶层明确声明`self_peer_model.mode=declared_service_fifo/1`。self连接的`src/dst`相同、两端NIC相同，仍须在所声明平面真实存在；删除`request_port/response_port`，增加`self_service.apply_ns/complete_ns`。二者是相对实际服务开始的声明时长，满足`0 <= apply_ns <= complete_ns <= max_sim_ns`；零时长合法，但不会因此生成网络ACK。本入口仍要求至少一个正常远端连接，不支持`bounded_buffers`。

每个self连接有独立FIFO：开始时间为本操作发布与上一项服务完成的较晚者，不占用GPU本地计算槽。发布时按原规则捕获源字节并保护至完成，apply回调用目标NIC权限修改共享内存，complete回调释放额度和源保护。可以依赖`applied/completed`，依赖self的`request_acked`会在网络分配前拒绝；显式前缀发布同样适用。

本例先从0向4写入整数5，实际接收触发同一self连接的加1、加2，原子旧值应分别为5、6。另一self连接独立写入119；同时存在100微秒本地计算。完成加2后，远端后继读取节点4的实际整数8并发回0。self服务应在计算结束前完成，后继不能用预填常量冒充实际源读取。

结果中self操作的`delivery=declared_self_service/1`，有`self_started_ns/self_completed_ns`，`request_acked_ns/response_ns`保持−1；目标应用、源捕获与lease事件仍进入共同内存记录。self不会出现在transport streams中，也不生成交换机包；普通远端连接仍走真实ns-3报文和ACK。

`rdma-context-plan`目前仍拒绝self；本例没有接入完整现代DeepEP或硬件校准。时长只是显式功能参数，不能用这个小例主张self的真实性能或论文收益。
