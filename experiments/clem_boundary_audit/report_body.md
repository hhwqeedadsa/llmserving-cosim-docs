# CLEM与我们的MoE网络仿真：代码能证明什么，研究还缺什么

2026-10-27。只读源码核查；本轮没有运行CLEM、网络仿真、GPU或构建。CLEM固定为 `4a91f8b655224c0b1d1c300aa09c7058f4da2707`；它声明使用的阿里ns-3 `dev/clem` 固定为 `434e4f2fe4bc2fb803a1dcda690869f66802f8b8`。结论仅针对所核查路径，不能推广到其他版本或未公开实现。

**结论：真实通信库与ns-3双向联动、持久连接复用和显存节省，CLEM都已有具体实现。我们的接入和功能补齐不能据此称创新。仍值得检验的是：针对现代分层MoE，能否用更少表示和处理成本，保留足够可靠的网络决策；两项候选目前均未成立。**

## 1. CLEM的实际调用链，比“读一条trace”更强

CPU侧 `ibv_post_send` 经QP的context函数表调用；补丁将该入口指向 `shm_ibv_post_send`。这是具体的host verbs截获，不是从README猜测。依据：{{host_hook}}、{{verbs_dispatch}}、{{post_send}}。

截获函数逐个遍历WR（发送工作请求），向共享内存发送 `IBV_POST_SEND` 命令，保留WR编号、操作码、flags、立即数和总长度，特殊PostFifo请求另保留控制内容及远端地址。网络端 `IbvInterface::PostSend` 将请求在声明的send latency之后放入 `IbvQP`。默认值为4000ns，不能当真实NIC校准值，也不能与本项目其他输入直接对比。依据：{{command_fields}}、{{submit_latency}}、{{interface_defaults}}。

WRITE / WRITE_WITH_IMM进入持续存在的 `RdmaQueuePair::m_messages`，并触发NIC发送；网络ACK推进 `snd_una`。当确认位置覆盖当前消息范围，`QpCompleteMessage → FinishMessage → WrCompleted` 才产生对应完成。依据：{{request_opcodes}}、{{persistent_qp}}、{{message_enqueue}}、{{ack_handling}}、{{ack_condition}}、{{completion_callback}}、{{message_finish}}。

必要的PostFifo控制结构由 `WrCompleted` 调用 `ExecPostFifo` 写入对端进程；signaled WRITE生成发送CQE，signaled WRITE_WITH_IMM还消费对端RecvWR并生成接收CQE。CCL侧poll从共享完成队列取回结果，推进实际通信库。不能把这条普通跨机路径说成“提交时就伪造完成”。依据：{{wr_completed}}、{{fifo_visibility}}、{{poll_cq}}。

还需区分特例：启用P2P且同一模拟服务器的64B PostFifo走直接控制写回及可选CQE分支，不经过上述完整网络路径。它是源码明确选择的机内控制处理，不能据此评价跨机网络精度。依据：{{p2p_fifo}}。

**对我们的直接影响：** 联动、共享QP、控制消息及完成回调都应列为强基线。我们目前的人工分阶段预约/准备/发布和字节级功能检查，证明的是自己的接口范围；它们没有胜过实际CCL执行的证据。

## 2. 已核查路径的三条边界

### 2.1 不能把host verbs截获直接等同于GPU直接发起路径

CLEM补丁针对NCCL 2.23.4系列，README指定基线 `2ea4ee94bfb04c886c79ccae60ac9961000fdee2`；本项目此前核查的DeepEP Hybrid配套GIN/GDAKI来自NCCL `v2.32.3-1`、固定提交 `12df1a11afad322be5a204a2db890161cbf8131d`。

旧入口能截获CPU调用，不能自动截获GPU写WQE（网卡工作描述符）、GPU门铃、CPU proxy或NO_DBR不同handler的发布行为。要接现代路径，应沿真实provider的数据结构建立适配，并保留：哪个context/peer共享QP、预约与连续ready前缀、何时发布、何时远端可见，以及何时允许源缓冲复用。这里存在版本和接口适配工作；不是证明CLEM原理上做不到，也不是证明我们的模型已覆盖真实GPU。

### 2.2 READ和原子操作不能只看verbs枚举里有没有名字

`IbvQP::EnqueueSendRequest` 对WRITE两种操作排队发网络；RDMA_READ明确按NCCL `ncclIbIflush` 自读用途直接生成CQE。`IbvCQ::GenerateSendCQE` 的switch只有WRITE、WRITE_WITH_IMM和READ，其余进入fatal分支。该具体执行链没有提供通用远端READ或fetch-add的执行与完成路径。依据：{{flush_read}}、{{completion_opcodes}}。

因此现代MoE的远端计数通知、原子返回值及它们驱动的后续工作需要独立适配。不能因为头文件定义了atomic opcode，就标成已经支持；同样不能因本项目有功能模型，就声称它比真实CCL更准。本项目事务路径已能模拟put/get/fetch-add、目标字节和回复完成，但没有GPU硬件校准，完整Hybrid控制、转发和Combine尚未联通执行。

### 2.3 同一QP的消息推进规则也需要匹配

已核查的 `GetBytesLeft` 仅提供队首消息剩余字节；ACK满足当前消息完成后，完成函数出队，再设置下一消息起点并触发发送。它会影响同QP不同消息之间能否重叠。依据：{{message_front}}、{{ack_condition}}、{{completion_callback}}。

这是需要与目标provider/NIC语义匹配的具体规则，不是本轮已确认的性能缺陷。尚未运行CLEM，也未取得相同输入的真实NIC对照，不能给出误差或改进百分比。我们自己的持久网络路径同样须接受这类检查，不能默认拥有相反的正确行为。

## 3. CLEM怎样扩大规模，96倍究竟说明什么

README声称单GPU仿真规模可扩大96倍，并列出hostname替换、memory reuse和多机器仿真。源码能够核实其中若干机制，不能在本轮复核96倍的测量条件或承诺同样加速。

在nccl-tests补丁里，发送区改为固定 `SIM_ALLOC_MEM_SIZE=8388608`，接收指针直接复用发送区，模拟hostname按每8个rank分组。设备AllGather、AllReduce和ReduceScatter补丁还将多处primitive的逻辑偏移替换为0，同时保留 `nelem`。这不只是减少一次内存分配，也改变了实际访问位置。依据：{{allocation}}、{{allocation_size}}、{{host_identity}}、{{gather_address}}、{{reduce_address}}、{{scatter_address}}。

示例启动脚本运行64MB AllReduce，选择Simple/ring并使用 `-c 0` 关闭数据校验。nccl-tests分配处原注释也明确说明模拟器不传CUDA数据、不关注数值正确性。它很适合研究经过限定的通信调度与网络反馈；不能把它解释为任意模型张量、地址和数值语义均未改变。依据：{{launch}}。

对MoE，许多数值payload可以不影响网络决策，但token映射、计数、tail/finish及Combine逆映射可能决定下一步往哪发、发多少和何时开始。安全的省内存办法须区分这两类内容：不影响未来通信的值可抽象；能改变未来通信的控制内容和未结束访问的生命周期须保留。是否保存完整数值，取决于目标可观察行为，不能一概要求所有张量逐位正确。

**96倍仿真规模 ≠ 网络事件减少96倍 ≠ 宿主速度提高96倍。** 当前入口仍安装每模拟节点的IbvInterface并实际排队消息。入口还有条件编译的MTP（多线程）及MPI钩子，不能声称阿里后端只支持单线程；本轮没核对它们启用时的全栈正确性、IPC时钟或扩展效率。依据：{{install_interfaces}}、{{parallel_hook}}。

## 4. 对照我们的现状

✓表示该限定范围有源码或既有功能验收；“待验证”不等于平台没有能力。未运行竞品，因此没有同机速度排名。

| 维度 | CLEM固定公开路径 | 本项目当前状态 | 对研究的要求 |
|---|---|---|---|
| 实际CCL与网络反馈 | ✓ GPU上执行修改后的CCL，host verbs与ns-3联动 | ✓ 功能前端/事务会话与同一次原生网络联动；未执行真实CCL/GPU | 真实库执行是精度强基线，不能只比固定流trace |
| 持久QP与ACK完成 | ✓ 有持续消息队列、ACK及CQ反馈 | ✓ 有持久网络、事务回复和源释放检查 | QP复用和重传不作独立创新 |
| GPU直接发布与现代原子语义 | 旧host截获及限定opcodes；现代路径需适配验证 | ✓ 部分逻辑预约/准备/前缀发布、原子回复功能；无硬件校准 | 按固定provider逐项匹配，不能标成全面超越 |
| 大EP分层MoE完整执行 | 本次核查没有DeepEP完整运行证据 | 尚未完成Hybrid控制/转发/Combine的完整联合执行 | 先补可信参考，不能声称现代千卡已可用 |
| 节省GPU/内存 | ✓ 固定buffer、地址改写；规模倍数为上游声明 | 无GPU运行；现有软件模型不等于该项已做更好 | 比较相同可观察行为及完整成本 |
| 网络事件/宿主扩展 | 有MTP/MPI入口钩子；本轮未运行验证 | 现有同步批次已有；无已验证的新事件缩减加速 | 不把少分配显存当网络仿真提速 |

本项目的特定价值目标是服务MoE推理的Scale-Out跨机网络决策：输入路由、batch、专家部署、服务器域/rail和分层带宽，输出瓶颈解释、完成时延及可比较的配置选择。真实路由不包含请求到达/EOS时间，声明计算成本不是GPU测量；15份完整路由也不能代表全部模型、任务和千卡生产负载。

新增13份路由的来源已由用户确认，和原4份同属 `core12345/MoE_expert_selection_trace/cognitivecomputations/DeepSeek-R1-AWQ/mmlu`。此前接收已登记17份，899与1479缺层，15份完整用于既有研究。本轮沿用该来源结论，不补下载、不重收数据、不重新声称远端修订已校验。

## 5. 两个候选如何重新限定，什么结果会淘汰它们

**候选一：保留网络决策所需行为的紧凑MoE表示。** 针对分层Dispatch/Combine，保留共享QP、控制值、实际发布、动态位置与逆映射、资源寿命；只虚拟化不会改变这些行为的payload或地址。目标是比完整执行表示更省内存/准备成本，同时不选错网络配置。现有完整DAG、符号buffer、普通生命周期复用、正确分层流模型以及经适配的CLEM都属于强基线；不是把host接入换成GPU接入就成立。

进入方法研究前，需先在小规模完成输入驱动的Hybrid参考执行，冻结能观测的操作/路径/字节、发布与门槛顺序、目标可见和源释放，再检验通用符号buffer与DAG已经能否同样紧凑。若普通表示已有相同成本及决策，或省略内容会改变后续控制，就淘汰或降为工程适配。收益同时报告表示量、完整准备与执行CPU、峰值内存、选择后悔值和误差；不把物理buffer缩小比例当最终加速。

**候选二：保留跨队列反馈的网络执行缩减。** 研究共享队列/缓冲、ACK、拥塞控制、恢复和应用门槛共同形成的交互范围，只有在可证明不跨越关键阈值的区间才合并处理；遇到不确定竞争则细化。它与候选一的区别是减少实际网络处理，而不是只减少输入描述。

基线须包含现有回调批次、队列批处理、静态分rail、阿里后端可用并行机制及相关的既有抽象方法。以前空闲跳时没有足够事件可省，简单FIFO类别的宽松界约3%，固定校验缓存也未达到事前明显收益门槛；这些旧结果不重开、不包装成正收益。如果正确交互范围总覆盖全网，或强基线已消除大部分成本，便淘汰该候选。最终同时测完整宿主加速/RSS/事件数与网络决策可靠性；GPU数、EP组大小、DP副本和batch分别扩展，不能把千卡EP4副本写成EP1024。

**目前能承诺的是可检验的目标，不能承诺已获得两个创新。** 本轮明确了更强的对照和一个有源码依据的内存/控制边界，没有产生新的网络优化收益、宿主加速或硬件精度数字。成熟机制的接入缺口应先作为平台工作补齐；研究价值仍须由它们之外的决策损失或可安全减少的实质处理证明。

## 6. 核查限制与失败保留

本轮只取固定CLEM补丁及十份网络关键文件，未获取/执行完整NCCL基线、timer实现、所有NIC/拥塞/并行模块。没有证明某一符号全仓不存在，也没有验证安装文档可执行、数值错误、攻击或真实系统漏洞。`ConsumeRecvWrAndGenerateRecvCQE` 内的赋值式条件值得维护检查，但已见调用方本来限制为WRITE_WITH_IMM；没有将它放大成通用安全结论。

原始raw README获取失败、空响应留存，改用已有本地README副本并核对其Git blob恰好等于固定tree。另一次ibv-interface.cc虽然完整下载，但单请求耗时20.886秒；README失败耗时20.410秒。初版urllib的10秒是socket超时，不能保证总耗时，两项原始失败收据全部保留。后续只取尚未尝试文件，并用子进程总截止修正获取器；没有重试旧URL。不能宣称所有获取均符合原10秒限制。

独立核验会重建Git tree标识、核对响应与blob、检查补丁行到目标行的映射和报告引用；离线恢复只读取归档源和复制研究代码，不重新HTTP、不运行上游脚本。该核验保证证据可追溯，不把自动检查成功当源码语义或运行性能已被机器证明。
