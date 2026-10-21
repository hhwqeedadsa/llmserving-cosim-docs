# Scale-Out 软件通信准入与发送资源记录

此例用于研究：限制尚未注入的跨机通信并发，会如何改变网络排队与Decode完成时间。它使用锁定ASTRA原生RDMA ns-3，不链接UB。它是软件限额实验，**不是硬件NIC/QP容量校准**，不能把限额数字解释为真实网卡规格。

先按[RDMA小例](../scaleout_rdma/README.md)构建，并从新构建的`backend.json`读取`service`。运行前加载项目Python环境，输出目录须不存在，运行卷预留8 GiB。不需要下载模型或路由数据。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/admission-topology
python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_rdma/service.json --topology /dev/shm/admission-topology \
  --protocol examples/scaleout_rdma_admission/fifo2.json \
  --backend "$COSIM_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/admission-service
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/admission-service --output /dev/shm/admission-diagnosis.json
```

`rdma-run`成功后自动核对业务依赖、字节、完成与资源记录；`rdma-check --run <目录>`可再次核对。失败目录仍可用`rdma-diagnose`，命令成功仅表示诊断生成。确定性丢包可与本例组合，规则见[故障小例](../scaleout_rdma_faults/README.md)。

## 修改配置

两个可选字段同时适用于`rdma/1`与`rdma/2`，未知字段拒绝。均不设置时保留原调度路径、字段及事件数。

| 字段 | 含义与范围 |
|---|---|
| `admission.policy` | `passthrough`、`fifo`、`destination_round_robin`。透传、按实际就绪入队顺序、按目的GPU轮转；轮转保持每目的GPU内部FIFO。 |
| `admission.max_active_per_source` | 每源GPU、跨其所有plane共享的活跃跨机消息数，0..1024。透传须为0，表示不限制；其他策略须大于0。没有新增带宽或原生硬件额度。 |
| `admission.decision_ns` | 每源串行的一次准入决策成本，0..1000000 ns。一次有成本决策最多准入一条，结束时才从当前就绪队列选择；占满额度时不反复轮询。透传须为0。 |
| `lifecycle_capture.max_events` | 1..100000；默认不记录详细生命周期。只有此字段而无`admission`时为透传观测。 |

策略只接收已经由实际SEND提交的身份、目的GPU、字节、就绪时刻，以及真实源端完成反馈。不会提前读取下一轮路由、接收端完成真值、图的未来消费者或全局队列。在线控制与离线诊断分开；后者不能作为前者的信息输入。

每条跨机消息只注入一次。数据连续接收齐备触发RECV消费者，**不归还发送额度**；完整ACK触发原生完成，待该发送QP从原生`m_qpMap`移除后，另一个同时间事件归还额度，再运行SEND完成回调。先处理已排队工作，再允许完成回调产生新任务。这里确认的是发送映射移除与软件额度释放，不代表接收QP、所有协议对象、GPU缓冲或KV已经释放；接收状态保留至Fabric退出。

同服务器分块crossbar传输不占本软件额度，保持原模型。缺少目标通信库和硬件测量时，不假定QP跨消息复用或共享DMA成本；每消息新建QP的既有范围不变。策略不抢占在途消息，不改包、路径、速率和完成定义。

## 结果与边界

`result.json`中的`admission.sources`给出各源终态的全部活跃身份、待发身份、决策事件和原生发送QP数。消息新增`admission_granted_ns`、`qp_registered_ns`、`sender_resource_released_ns`、`sender_qp_present`。未获额度的通信没有`first_tx_ns`，不能称其正在网络中等待ACK。

`lifecycle_capture.events`使用`columns`描述的8列数值数组：时间、事件、消息、源GPU、活跃数、待发数、软件限额、当时原生发送QP数。事件为申请0、获取1、QP登记2、数据齐备3、ACK完成4、发送QP映射已移除并归还软件额度5、决策开始6；决策事件消息为-1。

完整记录从起点重建全部额度占用者，容量大于1时保留集合，不随意指定一个“唯一阻塞者”。记录达到上限继续通信，`complete=false`与`omitted_events`说明截断；此时离线诊断只展示终态，不声称重建了完整历史。诊断内最多展开前256个非空占用者快照，显式报告其余数目，原始完整增量仍保留。

原生QP计数与软件额度分开核对：锁定`RdmaHw::QpComplete`在ACK回调返回后同步移除发送映射，软件额度稍后才归还。两个plane同纳秒完成时，第二个ACK记录可能仍显示两份活跃软件额度、仅一份发送映射；这是合法事件顺序。审计利用已核查的原生同步移除规则重放映射，不将其错误合并为一个释放动作。

这些观测没有人为仿真时延；真实策略决策成本必须通过`decision_ns`声明。记录与分析有宿主CPU、内存及输出成本，受原运行预算约束。额度更小可能显著拖慢业务，轮转也不保证优于FIFO；应固定请求、路由、拓扑与总资源，比较实际整轮完成及网络指标，保留负收益。
