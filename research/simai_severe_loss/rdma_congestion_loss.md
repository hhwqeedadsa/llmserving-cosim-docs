# 本项目原生RDMA后端怎样处理拥塞和丢包

当前SimAI服务使用固定ASTRA配套ns-3原生RDMA字节消息路径。它模拟RoCE风格机制，但不是实际NIC固件，也不是完整商用RoCE协议栈。下面区分固定上游功能与本项目适配，不把RTO重传称为创新。

| 情况 | 当前处理 | 来源与边界 |
|---|---|---|
| 交换机队列增长 | MMU维护入口/出口容量，ECN按配置阈值标记 | 原生SwitchNode/MMU；必须核实际阈值与队列，不能看到延迟就宣称拥塞 |
| 收到拥塞反馈 | 接收数据的ECN信息进入ACK/CNP反馈，DCQCN调整QP发送速率，再按定时器恢复 | 原生RdmaHw，当前入口只支持显式`cc_mode=1`；不是新拥塞控制 |
| 缓冲压力较大 | PFC按优先级暂停上游发送，满足条件后resume | 原生MMU/Qbb；可能传播等待，不能保证任意故障都无丢包 |
| 顺序正确的数据 | 推进`ReceiverNextExpectedSeq`并发送累计ACK | 原生RdmaHw；当前ACK间隔1字节是为任意短消息完整反馈配置 |
| 后续包先到、前面有缺口 | 不缓存乱序数据，发送受限频率的NACK；源将`snd_nxt`退回`snd_una` | 原生ReceiverCheckSeq/ReceiveAck/RecoverQueue，是Go-Back-N风格，不是选择性重传 |
| 尾包、反馈丢失或没有后续包揭示缺口 | 启用`recovery.mode=go_back_n`后，源端超时触发回退与重发，RTO退避；实际发送仍受NIC/PFC/DCQCN控制 | **本项目适配**`backend/rdma/recovery.cc`及SourceRecovery/Timer，不是声称原生路径自动具备完整尾部恢复 |
| 目标已收齐，但最终ACK丢失 | 重传重复数据到目标后，适配层补发当前累计ACK；不会重复增加有效接受字节 | **本项目适配**DuplicateAck；原生重复序号分支本身返回duplicate |
| 长时间无法推进 | 超过显式无进展重试次数后标记失败、保存原因与部分数据 | **本项目适配**有限RTO重试；当前示例4次，不伪造完成或无限等待 |

拥塞控制与可靠性恢复分工不同：降速/PFC用于控制队列与压力，ACK/NACK/RTO用于确认和修复缺失数据。人工注入的50%/75%接收丢包不是DCQCN产生的拥塞溢出；仅凭本实验不能评价商用NIC在同等丢包下的表现。

接收有效到达曲线累计各消息**连续接受**的字节；物理到达、不同字节曾抵达、源端累计确认分别记录。GBN会丢弃乱序数据，即使它物理到达也不能提前计入有效完成。这些是仿真接收协议事件，不是GPU内存可见时间。

固定源码入口：`third_party/LLMServingSim/astra-sim/extern/network_backend/ns-3/src/point-to-point/model/rdma-hw.cc`的`ReceiverCheckSeq/ReceiveAck/RecoverQueue/cnp_received_mlx/CheckRateDecreaseMlx`，`switch-node.cc`的`CheckAndSendPfc/CheckAndSendResume/SendToDev`，本项目`backend/rdma/fabric.cc`的协议属性配置与`backend/rdma/recovery.cc`的`Receive/Fire/DuplicateAck`。固定依赖身份见`deps.lock.json`。

观测边界：交换机入口/出口准入失败存在直接return丢弃路径，不保证其每次自然溢出都产生当前物理接收丢弃钩子事件。`fault_drops/data_loss`专指人工注入，不能冒充全网络自然丢包计数。严重丢包实验用目标设备的ReceiveErrorModel，确保指定故障有实际包和独立计数；若研究自然溢出，需单独补齐该路径的被动丢弃观测并验收。
