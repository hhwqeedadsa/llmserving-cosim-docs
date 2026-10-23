# 持久网络中的因果本地阶段

目标：在同一原生ns-3时钟中表达“实际网络回调→有界本地工作→后续写”，保留期间QP/反馈/恢复状态。新增公开stream-work/3，旧v1/v2行为与输出不变。不接现代DeepEP计划，不把accepted当GPU可见，不实现真实CUDA/provider，不执行论文收益实验。

v3沿用显式limits（最多128 streams、8192个总工作节点），增加必填local_model={mode:serial_per_gpu/1,duration_source:declared_ns}。全部work带kind。write保留id/stream/bytes/after；local为id/kind/rank/duration_ns/after。每GPU一个抽象本地执行槽，非抢占，按真实就绪顺序FIFO，同次就绪按输入顺序；不同GPU可并行，网络独立推进。duration_ns为0—max_sim_ns，显式声明耗时，不自动缩放或贴GPU型号。没有绝对开始/通信完成时间字段。

依赖只引用先前节点：write提供accepted/acked，local提供completed，跨类型事件拒绝。只有实际回调才能释放依赖；local在前驱齐备时入本地队列，等槽才开始，实际完成时解锁后继。未就绪write不reserve或发布。所有节点完成且物理网络排空后才关闭QP；应用完成与排空仍分开。达到仿真时限保留ready/start/end前缀、-1未发生字段，不能把未来定时器目标当完成。

v3输出增加local_work（id/rank/duration_ns/ready_ns/start_ns/completed_ns）、local_model、completed_local_work；顶层completed_work计全部已完成节点，persistent_transport.work仍仅计真实写。旧输出不加字段。原子、self-GIN路径、现代源buffer/可见性、实际计算并发/显存/硬件校准仍不支持。

验收：Python和native绕过CLI的类型/跨类型事件/时长/字段拒绝；人工小例以实际端点包和ACK重建write回调、本地就绪/串行排队/跨GPU并行及后继发布，检查本地阶段不产生包；两条持久连接各只创建一次、空闲追加时序号连续；重复输出一致；v1/v2与旧二进制实际结果逐字节一致；时限截断保留未完成与不偷发。固定默认及参考反馈模式都回归，不重跑真实Prefill/1024失败负载。

平台与研究分开，本轮仅平台。复用现有固定原生ns-3库/编译链接参数，只编译stream_probe目标，不改依赖、不安装/下载；旧二进制及库hash保留。每编译/检查命令60秒/RSS1GiB/地址2GiB，仿真5ms/结果16MiB；各运行64MiB，累计受监控300秒/192MiB，归档成员128MiB/压缩48MiB，卷预留8GiB。产物仅/dev/shm/moe-local-stages-20261023（易失，无持久备份），失败保留、不自动抬限重试。完整命令、来源snapshot、检查点、人工例与公开说明交付，本地提交不push；论文目标仍进行中。
