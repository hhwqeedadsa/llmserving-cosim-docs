# 同一plane的多网卡与共享Agg

本例有8个GPU端点、2条rail、1个plane和11台交换机。每个GPU有两张物理网卡，两组接入交换机共用Agg（汇聚交换机）和Core（核心交换机）。4条32KiB字节流经过同一个原生ns-3网络；其中GPU0的两张网卡同时访问GPU4的同一张网卡，不能用每个目标一条全局主机路由代替连接绑定。

接入与Tor–Agg为100Gbps，Agg–Core为10Gbps，用于产生可核查的排队和独立CNP（拥塞反馈包）。这是一组功能输入，不代表生产配置或完整MoE推理性能。

使用按当前锁文件构建的`stream_probe`，路径以构建目录的`backend.json`为准。编译工具、已有Protobuf路径及构建方法见[持久传输示例](../scaleout_rdma_streams/README.md#执行)。输出目录须不存在，父目录须存在。

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_multirail_native/topology.json --output runs/shared-rail-topology
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology runs/shared-rail-topology --protocol examples/scaleout_multirail_native/rdma.json \
  --config examples/scaleout_multirail_native/work.json \
  --backend build/native-streams/output/moe-rdma/ns3.42-moe-rdma-stream-probe \
  --output runs/shared-rail-result
```

本例三个JSON与[物理绑定验收](../../fixtures/physical_nic_binding/README.md)的共享拥塞条件逐字节相同：4条流完成，53个CNP实际经过网络。`result.json.physical_nic_addressing`列出GPU/NIC/IP对应，`persistent_transport.streams`列出每条流正反向网卡与地址；完整包捕获和端口/队列统计用于核对实际路径。

可改`fabric.rails/planes/upper_tier_scope`、交换机数量、链路带宽及工作中的`source_nic/destination_nic`，须同时满足拓扑、协议速率表和实际正反向连通性。`upper_tier_scope=rail`表示独立上层交换资源；改成`shared`会让rail竞争同一套资源，不能把两者的结果直接当作同资源收益比较。

物理绑定覆盖持久字节流、事务和现代功能入口；普通SEND/RECV继续使用原地址/路由域。同GPU/plane有多个NIC时按GPU与NIC分配`12.x`地址，源QP、ACK/CNP及事务回复固定使用声明端口。交换机在该目的NIC约束下按最短跳/ECMP转发，不按带宽加权；可选择较长但连通的源NIC。永久链路故障更新交换机路由，不能自动迁移到另一张网卡。范围及验收见上述链接。
