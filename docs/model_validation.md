# 模型依据与适用范围

持久流/事务/现代MoE入口支持基于GPU/NIC地址的实际物理绑定，覆盖同plane独立rail及共享Agg。原生本地地址注册验证接口归属，QP/ACK/CNP/事务回复保持指定NIC，别名目的路由仅从对应NIC进入GPU；路由重建不自动迁移网卡。单NIC完整结果保持，普通SEND/RECV继续使用原地址域。小型验收及故障边界见[使用说明](../examples/scaleout_rdma_streams/README.md#nic绑定和多rail边界)和[逐包证据](../fixtures/physical_nic_binding/README.md)。这是功能模型，未新增硬件校准或千卡现代应用证据；历史唯一首跳准入不再是当前多NIC限制。

现代native入口的默认逐批交付曾在四原始请求的四rank调用达到256次同步上限而失败，见[历史记录](../fixtures/modern_native/README.md)。显式callback-batches后，该单层调用及[新增请求batch4/8](../experiments/compact_network_choice/results/20261027/report.md)已完整执行，不等于完整模型、千卡或现代provider性能验收。数据为完整布局长度的合成标记；本地串行声明成本、功能信号地址和native-apply可见性均非实测GPU/provider。交付规则见[批次验收](../fixtures/native_callback_batches/README.md)。

显式self服务用于保留必要的同节点计数/屏障操作，声明时长不等于provider测量。小例核查FIFO、真实原子旧值、源捕获和远端丢包后的恰好一次应用；物理loopback/NIC争用、GPU可见、现代完整映射仍缺。见[功能验收](../fixtures/self_peer_service/README.md)。

context连接规划沿用[固定provider共享范围核查](reviews/现代连接共享范围与网络优化对象_20261024.md)中的显式connection/context/NIC规则；实际执行对象仍是MRT1。两NIC功能例验证身份复用与隔离、首包丢失恢复、目标字节和原子旧值，不证明GIN QP/报文等价、self-peer、GPU可见性或现代性能。见[验收范围](../fixtures/context_transactions/README.md)。

原生源NIC已修复两种唤醒错误：不可发送QP的旧`m_nextAvail`阻断其他QP，以及新增更早工作未提前旧定时器。真实双QP包/ACK小例证明旧实现依赖额外ACK才继续，修复后按实际限速时刻发送；15个CPU状态条件另核暂停、窗口、变速及同纳秒顺序。该功能证据不证明旧完整应用的全部源空闲或生产性能，既有结果仍对应其旧库身份，不能自动视为修复后结果。见[源NIC核查](../fixtures/nic_wakeup/README.md)。

`switch_links/1`是原生Scale-Out的永久交换机边故障功能模型：真实端口关闭、数据MMU回收、PFC恢复、在途到达丢弃与真实ACK/重传，配合声明延迟的集中最短路/ECMP更新。小例覆盖排队、headroom、多故障不同延迟、持久QP、未来事件与永久分区；[证据及限制](../fixtures/switch_link_failure/README.md)区分其功能范围。检测/控制报文、NIC故障、rail重绑定、GPU/MR恢复和硬件成本没有建模；经典改路与缓冲修复不是论文创新，未验证千卡或现代MoE性能。

固定NCCL官方文档要求窗口注销前保证对应buffer无NCCL访问；已有Revoke（保留资源）及SHRINK_ABORT（新通信域不与父域共享资源）必须作为恢复强基线。外部devComm的abort指针选择、GIN等待分支与DeepEP用户内核终止的区别，见[恢复适用性核查](reviews/通信域恢复与外部GIN内核边界_20261024.md)。该研究只执行精确CPU片段及记录替身，未实现真实恢复、证明用户CUDA生产者退出或校准成本。

`local_views/1`依据固定NCCL窗口/底层分配引用关系，只实现输入声明的本地视图和使用寿命：多个视图共享现有存储/注册。未执行可复用flags/handles/跨rank协商，未模拟provider退出、DMA fence或真实注销。最后视图关闭后真实MRT1包仍可通过静态权限写入，这是功能边界，不是上游漏洞。依据见[生命周期核查](reviews/窗口注销合法边界与底层注册复用_20261024.md)，功能验收见[本地窗口](../fixtures/application_windows/README.md)。

`nic_scoped_aliases/1`仅表达目标NIC作用域内的静态远端权限；依据是前期固定NCCL源码核对的同地址按连接/backend注册事实，不能把该事实等同于完整provider模型。人工双NIC实包核验共享字节、子范围、原子权限及反向返回，本地lease和副作用排序不能被rkey别名绕过。它没有模拟PD、查找后并发DMA、注销、重新注册、逐句柄失败或实测成本，不能证明生产漏洞或安全恢复；[验收](../fixtures/nic_registration/README.md)区分功能证据和后续缺项。

可选有界缓冲模式只限制命名的数据内容：注册窗口、MRT1重传副本、累计请求缓存和本地copy快照；源lease是引用，捕获临时向量峰值另列。元数据、解码临时对象、包、观测hex和报告副本均不在这些额度内，不能据此声称总RSS或GPU显存有界。ACK回收与失败背压已用实际包核查，尚无GPU/NIC容量校准、连续服务缓存淘汰或旧远端访问撤销保证，见[验收范围](../fixtures/bounded_buffers/README.md)。

在线控制timer已与声明的GPU本地计算槽分开：它只按原生时钟定时唤醒前端，未建模CPU调度成本、跨端通知、真实故障检测或GPU执行。人工对照验证复制不会被控制期限阻塞；这修正输入语义，不是恢复算法或硬件加速证据。取消与部分失败不改变未确认源缓冲保护，见[验收范围](../fixtures/session_control_alarms/README.md)。

事务输入的重叠写校验区分准备与远端副作用：prepared及其本地等待后继不证明跨连接旧写已应用；同连接的因果提交沿有序载体执行，真实完成可以覆盖这些前驱。真实单包丢失后的旧写覆盖反例和修复拒绝见[验收](../fixtures/transaction_effect_order/README.md)。这是功能接纳规则，不证明真实provider撤权、GPU可见性或跨代际恢复安全。

transactions/5保留现代布局对应的18MiB逻辑窗口，在人工数据程序上验证高偏移实际接收、复制和后继发包；完整现代事件、self及GPU可见性仍未接入。稀疏零初值、4KiB页和256页上限是有界功能存储规则，没有显存/页迁移/访问时延依据；驻留页字节不能当GPU内存或RSS。见[范围与验收](../fixtures/sparse_windows/README.md)。

在线事务会话已验证后端实际返回值能够决定随后构造的请求，并核对请求字节和目标内存；这只建立MRT1前端与网络反馈的功能连接。等待Python回复不推进网络时钟，但宿主处理仍消耗运行预算；不能据此宣称真实主机/设备提交没有成本。会话物理空闲保留QP，关闭才释放，避免把持久状态延续与资源清理混淆。它尚未连接现代DeepEP runtime，没有self物理服务、GPU可见或校准成本。见[验收](../fixtures/transaction_session/README.md)。

transactions/4以显式字节复制验证接收→本地输出→后继真实包的数据链路；入队至完成的输入/输出保护是本功能模型规则，不是从CUDA内核推断的真实读取时刻或性能。独立包/内存重放、排队与运行期覆盖反例见[本地输入输出验收](../fixtures/local_io/README.md)。专家数值运算、GPU内存序、GIN源安全与硬件成本仍未验证；没有copy的旧本地节点仍无输入输出保护。

transactions/3已通过实际包解码和独立内存重放核验接收内容的多跳转发。发布快照、提交至MRT1完成的读保护均为显式功能选择，不是GPU逐包DMA、真实CQ或provider成本。提交前覆写对照证明本地处理输入寿命仍未建模；测试通过不代表完整应用复用正确。见[源窗口验收与限制](../fixtures/source_windows/README.md)。没有新增GPU测量、现代应用或千卡规模结果。

transactions/2的本地处理同样使用声明耗时，区别是可以由实际解码接收窗口的LE64条件和消费驱动。条件入队时清零，排队不锁定数据，不能证明完整buffer寿命、GPU可见或数值计算正确；部分全局applied依赖只是功能编排。它未连接现代计划/provider或匹配GPU测量，不能当作现代MoE性能基线。功能反例、FIFO和截止核验见[事务本地阶段](../fixtures/transaction_local_stages/README.md)。

stream-work/3的本地阶段使用输入显式声明的ns耗时，每GPU单个非抢占槽，仅验证实际网络回调、资源排队和后续发送的因果关系。它不从GPU型号推导成本，不模拟SM/显存/CUDA并发，不把接收前缀当GPU可见，也未接入现代DeepEP计划或生产请求。旧v1/v2真实网络结果、人工端点包/ACK和截止前缀的独立核查见[验收](../fixtures/local_stages/README.md)。

固定原生mode1的反馈抽象与DCQCN 2015部署存在差异：`ReceiveUdp`逐标记数据包在ACK中置CNP位，此路径没有按流50us通知抑制；恢复仅有timer触发，未实现原文同时使用的发送字节触发。独立`ReceiveCnp`也未调用该mode1更新。ASTRA与Alibaba的10个控制函数去注释token一致，不能作为独立校准。已有Prefill长尾只能归于声明模型，不能直接推为真实MoE集群规律；论文参数也不是现代NIC通用校准值。源码、原文和既有包记录的核查见[反馈模型报告](review_artifacts/dcqcn_validity_20261022/report.html)，参考模式的要求见[实施门槛](../experiments/paper_readiness/dcqcn_reference_requirements.md)。

可选`figure7_reference/1`已在持久字节入口接入按流NP周期、实际独立CNP、RP降速及timer/实际发送字节两种恢复，ACK信用保持独立；CNP选路通过平台局部元数据投影补足原生未初始化哈希字。它是带声明取整、固定超增步长及原生帧成本的功能参考，未硬件校准，也未接现代GIN或在线推理。人工反向排队和状态重放只验收实现，不能作为新优化收益；适用范围及证据见[平台验收](../fixtures/dcqcn_reference/README.md)。

原生queue_fence将多连接已预留前缀映射到真实ACK/响应进度，避免动态等待集合包含未来工作。一个回调中的捕获及MRT1完成仍是功能选择，不能当作GDAKI逐peer取样、CQ或GIN flush精度验证。来源和反例见[队列快照验收](../fixtures/queue_fences/README.md)。

原生事务新增本地窗口等待，以实际解码后的LE64值决定功能程序是否继续，支持消费后清零。人工两轮验证及错误数据不放行不等于现代DeepEP或GPU可见性校准；模型没有给轮询添加实测代价。证据见[本地等待验收](../fixtures/local_memory_waits/README.md)。

独立远端事务入口已用实际包负载传递地址、key、数据、原子增量和旧值，目标功能状态按接收事件执行。MRT1帧、反向普通字节流及原子后等待请求ACK+回复均是显式功能选择，不等价于真实RoCE报文、Atomic ACK或GIN CQ；没有NIC原子服务时间、GPU可见性或现代应用映射。来源核查、重复应用检查与真实网络功能测试分开记录，见[远端事务验收](../fixtures/remote_transactions/README.md)。不能用此入口的时延判断现代DeepEP性能或方法收益。

独立持久字节路径已实际使用原生ns-3接收/ACK、DCQCN和包生成，保留连接对象与累计序号，不通过填入未来总字节阻止连接结束。后续工作在真实前驱回调后reserve，按ACK归还信用是明确的未校准功能选择，不能代替GIN flush、真实CQE或GPU源buffer寿命。它没有原子signal、目标窗口、现代Python事件接入或生产负载代表性，见[范围与实包验收](../fixtures/persistent_streams/README.md)。

现代direct的离线`modern-deepep-plan`固定新版本源码，按目的rank去重并采用完整BF16通信布局，保留计数块、屏障和未解析的接收slot/Combine通道公式。它尚未驱动GPU、队列核心或网络，不能作为现代DeepEP时延模型；支持的分支、声明的launch参数和未验证条件见[现代计划](../examples/modern_deepep_partial_order/README.md)。下面在线rank聚合及v1说明保留各自版本范围。

现代plan/1的同rank路径分类已发现错误：固定源码的signal与非NVLink专家计数块仍走GIN，GDAKI为self-peer创建回环QP；token与rank count才有本地指针旁路。plan/2已纠正分类并保留专家块发送区信息。物理回环路径与成本仍未知，不能推为交换机负载或零成本；历史结论的引用须附此[更正依据](../fixtures/modern_self_gin/README.md)。

窗口核心按固定源码将workspace与EP buffer映射到同一相对注册区，将GIN索引信号保留在provider独立域。原始BarrierSignals/EPSignals数据声明经替换CUDA限定词的宿主C++编译核对全部8个成员偏移；这只证明所选32/64位ABI的数据布局，未编译CUDA或测量硬件。事件驱动的计数/信号是影子状态，token数值、实际负载、GPU内存序及完整buffer寿命仍未验证；证据与检查边界见[窗口核心](../fixtures/modern_windows/README.md)。

新增事件核心执行slot双射、Combine通道内接收序号递增及Dispatch同token分配后的warp同步。最后一项来自固定`dispatch.cuh`中slot atomic后的`__syncwarp()`，不能由旧符号DAG中“每条put只依赖自己的slot”自动保证；旧静态闭包只代表必要依赖，不保证整个执行合法。事件核心的回调由调用者提供，功能测试中的人工完成不具备网络或硬件含义。验收见[动态执行范围](../fixtures/modern_deepep_runtime/README.md)。

原生Scale-Out新增逻辑字节准入额度，用于对照不同消息大小下的软件注入控制；这不是已校准的NIC在途窗口或GPU缓冲容量。交换机队列积分来自规范化的原生BEgressQueue变化，源QP快照和阶段耗时为执行证据。默认锁定RDMA库依赖NACK触发重传，没有通用ACK超时恢复；项目可选恢复扩展见下文，不能据此推断生产RoCE可靠性。配置和覆盖范围见[准入示例](../examples/scaleout_rdma_admission/README.md)。

本页说明仿真中的计算、通信和容量分别依据什么、采用哪些近似，以及现有验证能支持什么结论。运行方法见[用户指南](user_guide.md)，模块与执行顺序见[系统架构](architecture.md)，完整参考场景见 [CM384 / DeepSeek-R1](scenarios/cm384_r1.md)。

当前平台可以执行完整 Decode 计算与通信依赖图，并用 ns-3-UB 的实际完成事件推进后续任务。计算节点使用声明的耗时，不执行张量数值运算；通信使用独立实现的行为模型，不加载 CANN 或 DeepEP 内核。真实路由来源、参考模型的功能正确性和目标硬件校准是三项独立依据。

操作阶段与队列等待统计来自仿真中的真实节点、回调和队列状态变化，分别记录 VOQ 与 FIFO，不从流量形状推断等待量。小型原生执行已经检查统计启停的执行一致性和独立积分，证据见[操作与队列输入](../fixtures/m14_operation_queue/README.md)。这些信息在目标设备上的采集接口、精度及硬件开销尚未验证，仿真常驻内存与输出成本不能替代硬件测量。

聚合保存直接累计实际物理发包、队列状态与应用消息来源；业务数据、附随元数据、计数发布、完成标志、LOAD 就绪通知分别登记，协议确认不归为应用状态。纯聚合仅允许对齐地合并窗口；任意边界或时钟变化需要覆盖所选区间的详细事件及初始队列状态。小例已检查与详细记录等价、选择性保存、预算拒绝及旧详细数据兼容，见[聚合保存证据](../fixtures/m15_aggregate_capture/README.md)。应用消息分类和仿真内部来源只用于评价，不能作为已确认的硬件观测能力。

## 计算模型

### 非 UB Scale-Out 有限片段

可选[来源回放](../examples/scaleout_route_replay/README.md)保留原始专家选择，部署、到达和成本仍是声明条件。人工4卡输入已验证两轮反馈和等容量重排；之后已取得并核验四份真实R1-AWQ/MMLU请求原件，完成4卡、两层、两步的原生网络执行，见[四请求证据](../experiments/moe_trace_hotspots/results/four_requests_20261018/report.md)。四请求不代表训练或生产总体，也没有证明真实热点优化收益。`service/1`只覆盖1—4个连续MoE层，KV仍为通用片段声明；`service/2`要求来源全层为所支持的GQA MoE结构，按逐请求实际context计算声明KV与attention成本，不支持完整R1的Dense层/MLA。当前服务专家数上限仍为256；离线统计工具支持至1024，二者范围不同。

本节原默认值对应 `moe-scaleout-service/1`。新增 `/2` 的[完整形状示例](../examples/scaleout_clos128/README.md)来自锁定模型配置：Qwen3-30B-A3B-Instruct-2507，48 层、H2048、专家 FFN768、E128/Top8、Q/KV heads32/4、head_dim128。结构核算 30,532,122,624 参数；专家分片，attention/embedding 等组内复制，GQA KV 为每 token 98,304 B。显存检查为权重＋按请求块对齐的 KV，其余 workspace/激活/分配器开销没有硬件校准。完整依赖不等于数值模型执行，H100 标签不产生测量表。attention 声明成本含 context 和 Decode step 变化，不沿用旧片段的 MHA KV 近似。

`topology/2` 的同机互连为 GPU→非阻塞织构→GPU，分块并竞争源入/目的出端口，具有显式带宽、传播和 FIFO；没有 NVLink 信用或织构内部争用模型。机内交付没有 RDMA ACK，兼容 `ack_ns` 与 `receive_ready_ns` 相等。跨机仍走下述 RDMA 路径。按需 KV 元数据与终态序列化只改变宿主表示，不减少逻辑容量、消息或依赖。功能证据与硬件依据分别报告。

独立 `scaleout rdma-run` 入口执行 ready-KV MoE Decode 片段，不沿用以下完整 MLA/CANN 模型：默认 H=1024、FFN=4096、16 专家、Top-K=2、2 层4轮；attention/pack/expert/merge 为显式声明成本，GPU 类型仅是标签。context 用于请求与有限 KV 声明，不自动外推 attention 耗时。专家权重和 MHA 尺寸 KV 只覆盖该片段，不证明整模型可装入 A100 80GB。

通信按 token×expert 分别计贡献，同 rank 项本地完成，其余按 rank 聚合为不等长直接成对 All-to-All-v；不声称复现 NCCL 或 DeepEP 算法。全部组与轮次共用唯一原生 RDMA ns-3。连续接收完整字节放行 RECV，完整 ACK 结束 SEND；实际事件推进 ASTRA 和下一轮 LLMServingSim 调度。合成路由、有限片段功能验证、硬件校准分别说明。延迟ACK未验证；默认原生协议存在尾包、最终ACK和NACK丢失后可能停滞的限制。可选 `go_back_n` 扩展提供源端ACK进展超时、指数退避和有限重试，接收重复数据通过真实ACK重新确认；故障注入与离线诊断可用于对照。服务主基线仍是无故障，不声称生产RDMA恢复精度。输入、输出和资源范围见[非 UB 小例](../examples/scaleout_rdma/README.md)与[丢包小例](../examples/scaleout_rdma_faults/README.md)。

固定DeepEP v1.2.1低延迟路径的源码核查进一步明确了适用边界：该库按peer/本地expert选择已有QP，逐贡献发送数据，另发包括零token在内的count/flag，并用双buffer及CUDA事件控制复用。当前Scale-Out按rank聚合、每消息新建QP，没有显式表达这些应用通知；完整接收字节也未建模GPU flag可见与接收kernel完成。二者不能视为等价实现。该差异是抽象范围，不使已有抽象模型功能验收失效，也不由操作数量推断时延误差。具体固定源码、四请求核算和建模修正条件见[通信语义审计](reviews/DeepEP低延迟通信语义与建模修正_20261022.md)。

### 可选原生 RDMA 恢复扩展的依据

交换机缓冲预检只复刻固定模型的初始分配：每物理端口headroom为`((rate_bps // 1000000) * delay_ns // 8000) * 3`，每端口reserve4096B，合计严格小于交换机缓冲。依据为`Fabric::Fabric`和固定ns-3的`SwitchMmu`，不是硬件测量。Python入口提前拒绝不满足条件及算术溢出的输入；通过不保证动态无损、实际PFC传播成本或恢复性能。与既有实际原生端口的核对见[预检验收](../fixtures/fabric_buffer_preflight/README.md)。

[恢复示例](../examples/scaleout_rdma_recovery/README.md)明确声明字节序号 Go-Back-N 行为：实际发送后计时，源端只使用实际累计 ACK；新进展重置超时，旧反馈不延期，超时倍增并限制重试。接收端只根据自己的连续接收序号识别完整重复包，按原生 ACK 包格式重新确认。源端不读取接收端或交换机真值。实际发送仍由锁定原生 NIC 的仲裁、速率和 PFC 决定。

计时原则参考 [RFC 6298 第5节](https://www.rfc-editor.org/rfc/rfc6298#section-5)，它是 TCP 文献，仅用于说明 ACK 进展和退避原则；固定模式不估计RTT；新增可选估计采用相同平滑原则，但采样、舍入和超时下限另有明确声明，不实现TCP拥塞控制。[Linux v6.12 RXE 接收器 duplicate_request](https://github.com/torvalds/linux/blob/v6.12/drivers/infiniband/sw/rxe/rxe_resp.c#L1275)在重复 SEND/WRITE 时重新发送 ACK，[RXE 完成器](https://github.com/torvalds/linux/blob/v6.12/drivers/infiniband/sw/rxe/rxe_comp.c#L771)对未确认请求有限重试并在耗尽时报告错误。这些源码支持重试/重复确认的设计动机，不表示本实现复刻了 RXE 的 PSN、重试状态机或生产网卡。

功能验证覆盖尾包、最终 ACK、NACK、连续丢失、过短超时、同刻 ACK/超时、双 plane、资源释放和真实前端推进；覆盖范围见[验收](../fixtures/scaleout_recovery/README.md)。重试耗尽采用整个共享运行终止，保留资源到诊断/销毁，不模拟单 QP 错误向应用传播后的继续执行。默认原生模式逐字段兼容、记录截断不改变协议，均已单独检查。显式可选Karn RTT估计已加入：每QP单个待测原始包，重传/NACK/超时作废歧义样本，采用平滑RTT与偏差生成有界基准RTO；无配置仍固定超时。精确更新与条件证明见[推理通信脆弱性](communication_fragility.md)。这不是完整RFC6298/TCP或RoCE实现，尚缺硬件RTO校准、真实通信库分块、QP复用和千卡故障执行证据。

可选跨QP恢复历史仅传递本源先前有效RTT和超时退避形成的保守RTO，每源容量/年龄/LRU独立，目的地址包含plane身份；不预测ECMP下一条路径，不伪造RTT样本。目的端份额是软件逻辑占用上限，不是网卡硬件保护或请求级容错。公开配置与验证范围见[恢复与资源保护](../examples/scaleout_rdma_resilience/README.md)。两者可能减少重复发送或保护健康候选，也可能降低并发、延后故障检测；不能预设业务收益。

### 完整 Decode 图

Decode 指逐步生成新词元的解码阶段；MLA（多头潜在注意力）使用压缩的注意力缓存。当前完整模型入口 `moe-ub-configured-service/4` 使用 MLA `model/2`，张量并行 TP 和流水线并行 PP 均为 1，初态为 KV 已就绪。每个 rank 是参与分布式计算的逻辑成员，本平台不要求一个 rank 对应一个宿主进程。每个 rank 对本地选中的请求执行 embedding、各 Transformer 块、final norm、lm_head 和 sampling；没有本地请求的 rank 仍处理收到的专家工作与必要状态消息。

每块包含 attention norm、MLA 投影与缓存处理、上下文注意力、输出投影、残差、FFN norm，以及 dense FFN 或 MoE 分支。MoE 分支同时表达 routed experts 和本地 shared expert，二者结果都就绪后才能合并并进入下一块。`sampling` 表示末端阶段耗时，不生成文本或推断 EOS。阶段定义与生成实现见 [full_model.py](../python/moe_ub_cosim/full_model.py)。

完整模型成本采用 `moe-ub-full-model-cost/1`，`basis=declared_reference`。成本声明必须匹配模型结构、层型、维度与精度；除专家放置外，结构变化后需要重新声明成本，不能静默沿用。

| 计算对象 | 持续时间 |
|---|---|
| 按 Token 计费的首尾及逐块阶段 | 本地批次 Token 数 × 对应 `per_token_ns` |
| `mla_attention` | 本地各 Token 的 `(token_index + 1)` 之和 × `attention_per_context_token_ns` |
| routed expert | 该 rank 实际收到的 Token×专家贡献数 × `routed_expert_per_input_ns` |
| shared expert | 本地 Token 数 × 本层 shared expert 数 × 单专家参考成本 |
| CANN 局部处理 | 按声明的计数检查、重排、结果打包、本地交付和归约成本计费 |

正工作阶段要求正成本。没有 shared expert 的层，其 shared expert 和 shared/routed 合并成本为零，并省去相应节点。完整模型中 CANN 子层的 `attention_route_pack` 仅表示 Dispatch 量化打包，不能重复计入已创建的 attention/router；`expert_local_merge=next_layer=0`，Combine 检查与归约也只计一次。

可选 `endpoint_prepare` 表达已有成本尚未包含的端侧额外工作：Dispatch 按该 rank 本地 token 数、Combine 打包按该 rank 实际专家输入数计费，分别只加一次到已有准备/结果打包 COMP 节点。零工作量不产生额外节点；其他计算和本地交付成本不变。这是显式参考耗时，并非新增硬件测量；字段和边界见[后端说明](../backend/README.md)。

旧模板全部 COMP 节点竞争每 rank 的一个计算槽。A3 局部资源模板将声明的通信核分别建模，普通模型计算仍竞争一个 `gpu_compute` 资源；共享专家可与独立通信核工作重叠。完成顺序由依赖与资源共同决定。层间保持 rank 本地依赖，不增加全 rank 逐层屏障。整轮所有成员完成后，调度器才推进本轮请求。图执行细节见[系统架构](architecture.md)，阶段与结构检查见[完整模型测试输入](../fixtures/m10_full_model/README.md)。

### 测量表的适用范围

旧层段路径支持按实际形状查找 `operator_profile`。已保存的测量依据来自 NVIDIA A100 80GB PCIe、PyTorch 2.8.0+cu126 / CUDA 12.6：22 项 H=F=256、FP16 的串行 gated FFN 与 GQA SDPA MATH 形状。原始重复样本、测量方法、脚本与输入摘要的定位见 [算子与路由证据](../fixtures/m9_evidence/README.md)。

这些数值包括所选 eager 实现的发射间隙，覆盖的是相应算子形状。未覆盖形状会被拒绝，不按峰值算力折算或线性外推。当前 CANN 与完整 MLA 模型路径拒绝不匹配的 `operator_profile`；A100 小形状表不能用于 R1 的 H=7168、MLA、INT8 或 Ascend NPU。CM384/R1 参考场景仍使用明确声明的合成成本。

## 通信行为模型

### 通信模板的区别

| 模板 | 贡献与返回规则 | 依据和用途 |
|---|---|---|
| `direct_rank_dedup_v1` | 同一 Token 发往同一目的 rank 的多个专家时只发送一份输入；专家计算分别保留，按声明的本地加权合并约定返回一份向量 | 早期参考模型。已核对 DeepEP v1.2.1 的单机 normal 路径，但没有复现其完整元数据、通知、通道或 NVLink 时序。 |
| `cann_a3_v850_fullmesh_v1` | 每 Token×专家分别发送和返回，同一目的 rank 不去重；Token 所属 rank 收齐后归约 | CM384/R1 WRITE 基线采用的独立 CANN 行为模型，以下说明适用于此模板。 |
| `cann_a3_v850_native_v1` | 保留上一行的数据布局和状态消息；向量采用可选 STORE/LOAD，LOAD 前增加真实供数通知 | 本平台定义的原生操作映射，不能把新增握手或 worker 调度称为 CANN 真机行为。 |
| `cann_a3_v850_store_resources_v1` | STORE/STORE；按源码分核、有限局部缓冲及逐 Token 条件执行，分开局部复用和协议回收 | v0.5 平台参考模板；核数量和复制栅栏映射为显式假设，未硬件校准。 |

DeepEP 的固定源码版本为 `9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`。其四 rank BF16 调用配置、八项源码对照和已知差异保存在[核对证据](../fixtures/m5_model_audit/README.md)与[原始完整核对记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/model_validation.md)。这部分是历史模型依据，不能代替当前 CANN 规则。

可选 `producer_round_robin` 仅用于检验请求组织假设：按已验证的通信核静态分配划分逻辑序列，每端仍共用一个 worker、统一 STORE 额度和请求池。它不声明 A3 具有这些物理队列，也不修改状态到下一份准备的同步。默认 FIFO 同样不能仅因名称或默认配置被当成硬件依据。实现与配置见[后端参考](../backend/README.md)。

### CANN 来源与调用范围

固定来源为 [CANN ops-transformer v8.5.0](https://gitcode.com/cann/ops-transformer/tree/6ead121aded45355043b502756b6592fd7c30b14)，提交 `6ead121aded45355043b502756b6592fd7c30b14`。八个来源文件的路径、SHA-256、大小、调用配置与许可证摘要保存在 [source-evidence.json](../fixtures/m10_cann/source-evidence.json)。来源使用 CANN Open Software License Agreement Version 2.0；本平台没有链接或执行这些内核。

选择 A3 的 MoeDistributeDispatchV2 / MoeDistributeCombineV2，API 配置为 `fullmesh_v1`，使用非 `fullmesh_v2` 分支、TP=1、`expertShardType=0`。Dispatch 使用动态 INT8（`quant_mode=2`，无 `smooth_scales`），Combine 使用非量化 BF16（`quant_mode=0`）。不包括专用 shared expert rank、弹性或特殊专家、二维专家 mask、其他架构和其他量化模式。各 rank 使用相同物理输入槽数，通过前缀 active mask 表示活跃请求；零活跃请求不表示原生算子允许 BS=0。

下表的路径相对固定提交的 `mc2/`，D 表示 `moe_distribute_dispatch_v2`，C 表示 `moe_distribute_combine_v2`。

| 行为 | 固定来源定位 | 当前表达 |
|---|---|---|
| 内核选择 | D/C 的 `op_kernel/*_v2.cpp` | 只采用上述 A3、量化与 TP 分支。 |
| Dispatch 复制与地址 | D 的 `op_kernel/moe_distribute_dispatch_v2.h`，379–386、545–565、653–714 行 | 按 Token×Top-K 贡献发送，保留连续均衡分核顺序，同目的 rank 不去重。 |
| 计数和等待 | 同文件 `SetStatus`、`WaitDispatch`、`Process` | 每源 rank 向每专家发布 32 B 状态，包括零贡献。目标本地专家等待全部源计数与数据后重排；`SyncAll` 表示本地核同步。 |
| 返回顺序 | 同文件 `LocalWindowCopy`；C 的 `BuffInit`、`ExpertAlltoAllDispatchCopyAdd` | 按本地专家、源 EP 序号、源 Token 排列；Combine 均衡分核并在核内按 EP 序号旋转，每贡献返回一份向量和 32 B 完成标志。 |
| Token 归约 | C 的 `LocalWindowCopy`、`Process` | Token 所属 rank 收齐 Top-K 结果后加权归约；远端专家 rank 不先合并同 Token 的多个结果。 |
| 缓冲区 | D 的 `op_host/op_tiling/moe_distribute_dispatch_v2_tiling.cpp` 中 `CheckWinSize`；两内核 `BuffInit` | 保留数据、状态和 staging 空间，当前图完成前不复用。 |

上述规则由 [cann.py](../python/moe_ub_cosim/cann.py) 生成，并由 [cann_plan.cc](../backend/cann_plan.cc) 独立核对。具体测试与已知近似见 [CANN 测试输入](../fixtures/m10_cann/README.md)。

### 业务数据、应用元数据与填充

设 `A(n,a)=ceil(n/a)×a`，H 为向量宽度。B 表示字节；下列复制长度属于应用载荷，尚未包括 UB 包头、协议控制包或多跳重复计数。

| 消息 | 业务字节 | 应用元数据 | 填充及实际复制长度 |
|---|---:|---:|---|
| 动态 INT8 Dispatch 向量 | H | 4 B scale + 12 B rank/token/topk 三元组 | 填充 `A(H,32)−H+28` B；实际复制 `A(H,32)+44` B |
| BF16 Combine 向量 | 2H | 0 | 实际复制 2H B |
| Dispatch 计数状态 | 0 | 32 B | 包括零贡献状态 |
| Combine 完成标志 | 0 | 32 B | 数据返回后的状态发布 |

Dispatch 数据窗口的地址步长为 `A(A(H,32)+44,512)`；Combine 为 `A(2H,512)`。地址步长尾部的空隙不会自动成为发送字节。状态消息属于应用元数据，不能计为业务归属，也不能混同 UB 的 ACK 等协议控制包。

`chunk_bytes` 只分割跨芯片向量消息；每个状态发布仍是单个 32 B store。集合通信分块、TA 分段与 TP 包载荷属于不同层次，后两者由[后端配置](../backend/README.md)控制。端口整包字节还受协议头、控制包与经过的交换出口数影响，不能直接当作唯一业务交付量；计数定义见[记录格式](record_storage.md)。

### 依赖、完成与并发

可选发送资源记录读取仿真器的实际调度队列和占用集合，能验证当前参考发送槽模型中的等待与释放；它不代表已测得硬件队列或发送引擎状态。就绪且满槽可以确定模型内的等待条件，不能单凭此项确定网络拥塞、协议确认等具体原因。记录口径见[发送资源状态](record_storage.md#端侧发送资源状态)。

同 rank 贡献保留专家计算及本地依赖。同芯片的不同 rank 通过本地交付事件和显式准备耗时连接，不生成 ns-3 链路、包或端口计数；这保持了跨芯片 Scale-Up 网络的研究范围。

跨芯片分块使用 `URMA_WRITE/RTP` 参考映射。Dispatch 数据由 Token 所属 rank 发往专家 rank，Combine 数据反向返回。发送方只提交一次事务，接收节点只登记等待。目标 `RemoteWriteCommitted` 累计达到实际载荷长度后表示数据可消费；源 `transaction_done` 才表示发送完成并释放发送资源。源数据发送完成后发布 Dispatch 计数；对应返回数据完成后发布 Combine 标志。不能将接收就绪与发送资源回收合并为一个回调。

旧模板的 `aiv_lanes` 为 1～48 个参考逻辑发送序列，不是测得的 AIV 分配数。每个序列等待前一事务完整结束才复用，另受每 rank 1 或 2 个发送槽限制。旧模板逐 rank 归约等待本地整个 Combine 阶段；这些行为保留供历史结果复现。

v0.5 模板按固定源码的 SplitToCore 分配贡献与 Token，保留 Dispatch 跨核数据栅栏及 Combine 核内次序，允许无关核独立工作和已到齐 Token 先归约。准备、重排、打包、状态检查与归约的累计成本保持原定义；它们的重叠可以改变总耗时。局部复用等待原生请求成功形成，默认复制栅栏等待目标数据交付，协议资源仍等待原生响应回收。该映射不等于模拟了真实 MTE、HBM 或缓存，来源与剩余假设见[固定源码核对](../fixtures/m18_a3_execution/README.md)，字段见[后端参考](../backend/README.md#a3-局部资源模板)。

URMA WRITE 是平台对通信行为的明确映射，不能据此声称复现原生 MTE/LDST。独立的 `cann_a3_v850_native_v1` 将向量映射为 ns-3-UB STORE 或 LOAD；状态和 LOAD 供数通知使用 STORE，并通过原生请求/响应回调判断数据与资源完成。LOAD 的 32 B 就绪通知、按 worker 均分任务和整包响应短尾填充均是明确的平台/后端参考行为，未经过 CANN 或 CM384 硬件校准。URMA READ、CTP、XCCL、CANN 超时、故障处理及原生标志复用竞态仍不在已验证范围。

### 通信缓冲区与输入限制

CANN 数据窗口下界保留 host tiling 的最大 2 字节类型预算，即使 Dispatch 使用 INT8。令 B 为每 rank 批次上限，E 为总专家数，K 为 Top-K，L 为参考逻辑发送序列数：

```text
data_min = 2 × B × [E × A(A(2H,32)+44,512) + K × A(2H,512)]
staging = 2 × L × A(A(H,32)+44,32)
cann_buffers = declared_data_window + 1,048,576 + staging
```

上述 `staging` 适用于旧模板。v0.5 模板按源码动态量化的占用公式与 170 KiB 阈值选择每核 1 或 2 个 Dispatch 缓冲，Combine 为 2 个缓冲；容量预留改为 `cores × [dispatch_buffers × A(A(H,32)+44,32) + 2 × A(2H,32)]`。数据窗口及各层保守求和规则继续保留。缓冲数依据源码公式，通信核数和本地处理耗时仍是参考参数。

声明的数据窗口必须不小于 `data_min`。容量模型还保留更大的向量及发送槽下界。令 R 为 rank 数，S 为每 rank 发送槽数，sD/sC 分别为 Dispatch/Combine 每元素字节数：

```text
communication_minimum_per_layer = max(
    B × R × min(K,R−1) × H × (sD+sC),
    2 × S × chunk_bytes,
    cann_buffers
)
```

完整模型将各 MoE 层下界保守求和，不假定跨层通信缓冲区可以重叠复用。该计算见 [model_capacity.py](../python/moe_ub_cosim/model_capacity.py)；它是容量预留规则，不能解释为每层实际发送了这么多字节。

当前 CANN 配置要求 H∈[1024,8192]、EP∈[2,768]、K∈[1,16]、B∈[1,512]、E≤1024；专家按 EP 顺序连续、等量放置。实际运行还受显式每图和累计预算限制。旧测试输入的 4096 次交付上限不代表所有新入口仍固定在该上限；声明更大预算也不等于已测量宿主内存或完成目标运行。

## 模型容量与 KV

容量计算使用模型结构、精度、专家放置和显式设备预算，分别扣除驻留权重及 scale、通信缓冲区和工作区，余量进入每 rank 唯一 KV 池。请求在接纳前按声明终点或有限观察片段预留 KV；驻留但本轮未选中的请求仍占容量，退出时只释放一次。不会把不足容量的请求悄悄截短。

旧 `model/1` 保留普通 GQA 布局；`model/2` 增加逐张量存储与 MLA 压缩 KV。对于当前 MLA 参考布局，每层缓存归一化后的 latent 向量及共享 RoPE key，不按注意力头数复制：

```text
KV_bytes_per_token = Σ层(latent_dim × latent_element_bytes
                         + rope_dim × rope_element_bytes)
KV_bytes_per_block = block_tokens × KV_bytes_per_token
request_reserve = ceil(reserved_token_count / block_tokens) × KV_bytes_per_block
```

普通已知长度请求的 `reserved_token_count=request.output−1`，即声明的最终总序列长度减 1；这里的 `request.output` 是调度器的总长度字段。真实路由观察片段使用 `prompt_tokens+first_decode_entry+decode_steps−1`，容量保证只覆盖所选片段，不将其改写为真实 `target_total_tokens`。预留不推进已计算长度，后续 `add_decode` 复用同一个块池，不再次分配相同容量。逐请求块对齐后再相加，不能先对平均长度取整。RoPE 表、临时展开或反量化矩阵、运行时元数据计入单独工作区预算。当前没有 HBM 带宽竞争模型，容量通过不表示量化内核已执行或实机可分配 HBM 已测得。R1 的固定张量形状、精度及生效预算见[场景容量参数](scenarios/cm384_r1.md#容量参数)，实现与接纳检查见 [mla_capacity.py](../python/moe_ub_cosim/mla_capacity.py) 和[容量测试输入](../fixtures/m10_mla_capacity/README.md)。

## 路由来源与观察范围

合成路由用于小型功能检查；公开模型路由用于保留真实专家选择分布。导入时验证文件身份、完整层/Token 顺序、专家范围和唯一性，不循环、补齐或拼接缺失记录。真实来源文件不包含本平台选择的网络、计算成本和线上请求到达过程，不能据此声称整个运行来自同条件实机采集。

Decode 入口与请求完成分别处理。文件尾端或观察片段结束均不推断 EOS；未知 `target_total_tokens` 保持未知。观察退出表示停止继续模拟该片段，不代表模型已生成结束标记。CM384/R1 的数据集固定版本、选择算法、Token 索引和精度差异见[场景路由说明](scenarios/cm384_r1.md#请求与专家路由)。

## 验证结论如何使用

| 依据 | 当前能支持的结论 | 不由该依据证明的内容 |
|---|---|---|
| 固定源码及逐项行为核对 | 贡献、字节、等待和资源规则有可追溯来源 | 原生内核时序、数值正确性和硬件性能 |
| Python/C++ 独立计划核对及小场景执行 | 输入约束、依赖、实际网络完成反馈、资源释放与观测不扰动执行 | 未运行的规模或不匹配配置 |
| [CM384/R1 WRITE 首轮验收](../fixtures/m10_research_slice/README.md) | 完整目标首轮依赖图、已保存图的后端重放、1 µs/10 µs 观测及记录守恒可重复 | 完整 64 步服务、稳态和目标硬件绝对时延 |
| [CM384/R1 原生首轮验收](../fixtures/m16_native_target/README.md) | STORE/LOAD 与逐包喷洒的完整首轮执行、42 所选端口聚合、分类来源和有限资源内公开导出 | 全端口详细记录、第二次完整原生目标重放或硬件校准；重放等价由独立小例检查 |
| [A3 资源与目标层段验收](../fixtures/m21_platform/README.md) | STORE/STORE、四类因果小测试、采集启停一致性，以及保留目标部署的 128/1024 请求连续三层执行和保存 | 新资源模板下的完整 61 块、64 轮服务或目标硬件校准；后端重放等价由独立小例检查 |
| 真实 R1-AWQ 路由和限定 A100 算子测量 | 相应路由来源真实、相应测量形状有设备依据 | CM384 INT8 部署同条件采集或 NPU 校准 |

`runtime_adapted` 表示固定通信源码行为已转为本平台计划；`full_model_graph_execution_validated=true` 只在相应完整图实际完成后写入。`unvalidated_reference` 仍表示未经目标硬件校准。`full_model_execution_validated`、`target_runtime_executed`、`numerical_model_executed`、`mla_kernel_validated`、`quantized_kernels_executed`、`target_deployment_validated` 等完整模型或原生执行标志仍为 false，不因图执行通过而改写。

开展参考参数实验时，应记录固定输入与版本、成本和网络选择、采样口径及结果适用范围。若要进一步声称复现目标硬件，需取得匹配形状、精度、并行布局和通信路径的测量，先固定事件对应、时间戳精度与字节口径，再分别比较数据量、阶段时间和端口分布。当前没有拟合目标时延或设定统一硬件误差百分比。

## 有限层段的初态与容量

有限层段复用原模型、完整部署容量、专家放置、原始路由层号和所选 Decode 步骤上下文。假定首块输入及 KV 已就绪，从空网络开始；首层不自动等同于充分预热，也不宣称稳态。只执行选定完整块的参考成本，不含 embedding、末尾 head 或采样。局部读完成采用全部原生 STORE 请求已形成的参考事件，业务与状态到达按真实接收字节区间核验；硬件 MTE/HBM 时序仍未校准。公开流程证据见 [M20](../fixtures/m20_segment_facts/README.md)，真实路由的目标规模执行与剩余参考假设见 [M21](../fixtures/m21_platform/README.md)。

显式`transport_recovery`把同一源端计时核心用于持续增长的已发布字节前缀，源端只读实际TX及累计反馈。接收端完整重复包补ACK而不重复交付字节；MRT1原子/响应及在线后继已在人工丢包场景核验，失败保存部分状态。故障规则的message_id在此路径表示stream ID。该模型没有连接重建/跨故障代际、GPU可见性或真实故障率含义；配置与验收见[持久恢复](../examples/scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)。
