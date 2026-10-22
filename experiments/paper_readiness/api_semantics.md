# 通信库语义核查：先验证模型，再设计方法

这是G1的初步源码审计，不表示已完成真实库复现或校准。依据当前主仓源码与固定DeepEP v1.2.1 README（提交`9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`）；后者文本快照hash见审阅sources。

| 对象 | 当前模型的可核实行为 | 目标库已知信息 | 必须补的证据 |
|---|---|---|---|
| 逻辑消息与QP | `backend/rdma/fabric.cc`的Send登记唯一逻辑消息，Inject逐次调用AddQueuePair；完整ACK后的清理才ReleaseSender | 固定README的get_buffer复用Buffer，低延迟Buffer创建时按本地专家数配置num_qps_per_rank | 固定内核/运行时的连接创建、复用、序号及拥塞状态；逻辑消息边界能否复用同一QP及其RTT状态。不能从README直接断言完整生命周期。 |
| 数据可消费 | CheckReceived按原生接收连续字节数触发received回调 | dispatch返回接收数据、事件或接收hook，正常与低延迟模式不同 | 传输接收、GPU可见、通知/flag、CUDA事件之间的真实必要依赖和代价。 |
| 发送完成与额度释放 | Acked确认QP已完成，调度清理后回调done；软件额度保持到ReleaseSender | 固定示例声明Buffer容量、异步事件，未提供本项目3条/16KiB额度依据 | 区分物理QP、WQE/队列深度、通信缓冲、应用消息及复用条件，确定什么资源实际绑定、何时释放。 |
| 步间边界 | service Pump等待EP全部rank完成、Messages::Complete和可装载新图；Python收到completed后才下一步schedule | 固定示例有previous_event、async_finish和return_recv_hook，并示意双micro-batch重叠 | 哪些buffer/event必须等，哪些跨请求操作可合法重叠；不能把存在hook推成任意提前完成。 |
| GPU支持 | 实验成本为声明值，gpu_type标签未校准 | 固定README列A100 intranode only，低延迟跨机示例在H800/CX7 | 选定可用设备/软件与被支持模式；机内A100测量不替代该版本跨机低延迟校准。 |

对论文判断的直接影响：当前每消息建QP可能放大冷启动/零RTT样本问题；固定图边界可能改变ACK尾部向后续请求的传播。它们都需要真实语义支持，不能凭“更保守所以安全”当作精度依据。保守模型也会改变策略排序。

下一步只做能消除上述歧义的最小核查：固定内核与运行时源码；标注一个连续两次dispatch/combine调用中的Buffer、QP、数据flag和事件；在受支持设备上取得重复调用及并发调用的实际记录；据此修正模型或明确限制，再重跑受影响研究。内核或实测资料不足时记录未知，不先实现QP复用、移除步间边界或提前释放。

本轮没有修改平台行为或第三方依赖，也没有把真实库README当作已完成硬件验证。
