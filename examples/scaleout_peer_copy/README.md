# 机内字节转发与持久跨机通信

本例验证 GPU0/1 → 同服务器 GPU2 → 另一服务器 GPU4 → 同服务器 GPU5 → GPU0 的字节传递。跨服务器使用实际原生 ns-3 报文，服务器内使用共享端口的分块 crossbar（交换结构），全部工作共用一个仿真时钟。源字节来自实际窗口，后续发送不能重新生成标记。

## 输入与限制

`work.json` 沿用 `moe-rdma-transactions/5`，增加：

```json
"peer_copy_model": {"mode": "topology_intra_host_crossbar/1"}
```

local 节点的 `copy.target` 可指定 `owner`，表示同服务器的另一张 GPU；source 始终属于节点的 `rank`。没有 owner 的 copy 仍在同一 GPU 上执行。不同 GPU 可使用相同虚拟地址，访问冲突按 owner、地址和长度检查。

peer copy 的 `duration_ns` 必须为 0，耗时由拓扑的 `intra_host.rate_bps/delay_ns/chunk_bytes` 决定。源 GPU 的串行工作 lane 占用至实际交付，目标端口同时被其他转发争用；此处不把机内字节发送成机外 RDMA。源读/目标独占写在准入时获得保护，开始时捕获源字节，交付时提交目标并释放保护，然后发出 completed。

拓扑必须为显式启用机内模型的 topology/2；跨服务器目标、自身目标、未知 GPU、非零固定 duration、owner 覆盖源字段和越界地址均拒绝。该模式暂不与应用视图或 bounded copied-buffer pool 组合；NIC 注册权限不等价本地 peer 访问撤权。每次 copy ≤32KiB，沿用事务连接/工作/窗口上限；peer 数量还受协议 `max_messages` 约束。普通 stream-only 入口保持拒绝机内旁路。

## 公开运行

在仓库根目录设置 `PYTHONPATH=python`；其他 cwd 使用本仓库 python 目录的绝对路径。先按[用户指南](../../docs/user_guide.md)的 `scaleout rdma-build` 构建固定原生后端，读取 `backend.json` 中的 `transaction_probe` 和 `transaction_session`。以下示例的占位路径需替换为实际绝对路径，输出目录必须不存在。

```bash
python3 -B -m moe_ub_cosim scaleout topology \
  --config /ABS/REPO/examples/scaleout_peer_copy/topology.json --output /ABS/NEW/topology
python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology /ABS/NEW/topology --protocol /ABS/REPO/examples/scaleout_peer_copy/rdma.json \
  --config /ABS/REPO/examples/scaleout_peer_copy/work.json \
  --backend /ABS/BUILD/transaction-probe --output /ABS/NEW/probe
python3 -B -m moe_ub_cosim scaleout rdma-transaction-session \
  --topology /ABS/NEW/topology --protocol /ABS/REPO/examples/scaleout_peer_copy/rdma.json \
  --config /ABS/REPO/examples/scaleout_peer_copy/work.json \
  --backend /ABS/BUILD/transaction-session --output /ABS/NEW/session
```

session 的依赖前端只在收到实际回调后提交后续节点，主程序直接执行同一依赖图；两者都只运行一次共享网络。改变机内带宽/传播/chunk 用 topology.json，改变物理各层带宽用 links，改变字节和依赖用 work.json；不要再为 peer 添加固定延时。协议已经限定 1ms 仿真、60 秒宿主时间、RSS1GiB、地址2GiB和16MiB结果。外层运行也应使用公开 host_probe 限制累计产物与8GiB磁盘保留。

## 观察结果

- 两条同时开始的128B转发共享 GPU2 目的端口；按本例声明的8Gbps、64B分块、每跳10ns，分别在276/340ns完成，目的端口繁忙256ns、最大等待128ns。
- GPU4 只有在实际收到首条256B远端写入后才开始 peer 转发；该256B转发耗时340ns，完成后 GPU5 才从实际窗口发回数据。
- peer 共交付512B；两条远端256B写入各有32B MRT1头，合计576B源载体，不含网络ACK及各跳头部。peer 本身没有物理RDMA包或ACK。
- `local_work`、`transactions.local_memory_events/source_events/target_events`、`intra_host_links`及实际源报文可相互核对。messages 的本地兼容字段 ack_ns/ack_callbacks 只表示数据就绪，具体解释见[输出说明](../../docs/record_storage.md)。

`without-peer.json` 与 `without-peer-topology.json` 是关闭机内旁路的普通123ns同GPU copy兼容见证。

这是人工字节与资源功能例，不是完整 Hybrid DeepEP、真实 NVLink/TMA/GPU 内存一致性、GIN/RoCE 成本校准，也没有证明论文创新或性能收益。完整分层应用的 slot、通知、屏障和 Combine 执行仍需后续接入。
