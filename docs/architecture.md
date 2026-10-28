# 系统架构与执行过程

独立`simai_service.py`加载固定SimAI/Vidur真实Cluster/Orca/Batch/Request，按原始专家选择生成有限MoE图并提交既有ASTRA原生RDMA服务。两个或多个EP域共享一个ns-3时钟和网络；仅实际完成回调允许推进Batch及下一轮。上游生命周期使用`1.0 + now_ns/1e9`秒，保存网络绝对纳秒；不叠加上游预测的通信时长。稀疏源rank与实际专家执行rank分别保留，计算为声明参考而非上游性能预测器。输入和边界见[SimAI联合小例](../examples/simai_service/README.md)。

独立多连接 `rc-fabric-probe` 在同一 `Fabric` 中保存各 QP 的序号、WRITE 进度、原子重放和定时器，多个目标 QP 共享同一 GPU 注册区字节。源完成由本连接的实际反馈决定，不添加跨 QP 隐藏顺序；请求共用物理 NIC 调度，回复共用物理反馈队列。有限功能范围见[多连接参考](../examples/rc_concurrent/README.md)，没有接入 LLMServingSim 或改变旧入口。

独立`rdma-rc-probe`将WRITE/原子请求和Atomic ACK放在同一原生`Fabric`时钟与物理队列中：目标从收到的操作字段/字节更新内存，源从实际反馈完成，有限重放缓存防止重复原子副作用。该入口只检查单QP顺序WQE；没有将MRT1、modern控制器或LLMServingSim连接到RC路径。固定速率调度、线格式近似及公开命令见[操作包小例](../examples/rc_packet_probe/README.md)。

显式 peer-copy 事务保留源 GPU 串行 lane，准入时保护源读/目标写，开始时捕获字节，经 `Fabric::SendPeerCopy` 复用 `SendLocal` 的分块机内端口。实际交付先提交窗口并释放保护，再发出 local 完成通知；网络接收可触发该转发，转发完成又可触发后续持久 RDMA。纯 stream 入口仍拒绝机内旁路，混合模式的远端事务始终走物理网络。详见[配置与范围](../examples/scaleout_peer_copy/README.md)。


`hybrid_deepep_plan.py`提供独立分层逻辑表示：逐token按服务器域去重，入口绑定源卡同位置rail，域内再按GPU去重，并保留三个Combine贡献分支。`hybrid_deepep_progress.py`核验显式可见性动作与Dispatch转发器的轮询、缓存tail和每批最多6条；选中未就绪来源不能跳过。它不使用已有truth生成输入，也不生成时间或最终GPU slot，尚未连接provider/native执行。完整范围见[示例](../examples/hybrid_logical/README.md)，不改变下方direct执行路径。

`hybrid_deepep_bindings.py`在上述输入准入后，编译Dispatch的持久GIN资源身份和逐peer API顺序；context到connection/设备、rail到world/provider peer分别转换。相同devcomm跨call共享，self计数仍为GIN，而本地token/进度分别是copy/atomic。显式DB/NO_DBR handler仅限定逻辑前缀语义，不确定预约、门铃或网络时刻，也未接入native执行；见[绑定输入与边界](../examples/hybrid_bindings/README.md)。

事务会话可选持有一份`StaticStreamValidationCache`，仅复用纯Python传输声明校验。不可变键覆盖完整拓扑、协议和派生streams，并区分内建JSON类型；不保留可变别名，不共享全局状态。完整动态候选与原生校验照常执行，缓存不参与网络控制。公开选项、计数和默认行为见[固定校验复用](../examples/modern_deepep_native/README.md#可选的固定校验复用)。

现代原生入口可使用显式的逐调用/rank专家阶段耗时矩阵。在实际Dispatch完成后，专家节点进入既有每GPU串行lane，真实完成回调再解锁Combine；矩阵不控制网络时刻、不替代网络反馈。固定成本v1保持兼容，v2输入与来源边界见[专家阶段成本](../examples/modern_deepep_native/README.md#按调用和rank修改专家计算耗时)。

持久传输在创建QP前为本地地址注册实际NIC归属；同GPU/plane多NIC使用物理别名，目的路由只从指定NIC进入GPU。发送、ACK/CNP与事务回复共享同一Fabric的实际端口、交换机和MMU；重建路由保留绑定与持久状态。普通SEND/RECV仍使用原路由域，范围见[多rail示例](../examples/scaleout_multirail_native/README.md)。

原生事务会话每次网络通知仍正常推进依赖、本地条件和排空检查。操作状态收集通过`RemoteTransactions::TakeOperationProgressUpdates()`消费实际变化的id，按原id/阶段顺序交付回调，再与本地事件和控制闹钟稳定按时间排序；最终`Report/OperationProgress`仍读取全量状态。增量索引不是前端可见的调度信息，也不改变网络或GPU模型。

现代事务入口可选复用HostTiming，分别记录Python控制推进和原生session/Simulator宿主阶段。观测不参与就绪、时钟或选路；同进程嵌套和跨进程等待不能相加，失败只解释已闭合阶段与最近快照。见[现代入口](../examples/modern_deepep_native/README.md#可选的宿主开销观测)。

`modern_native.py`提供功能接入：现代事件由同一次原生事务会话的实际回调推进，本地copy/计数清零/声明计算也在该时钟执行。必要self、动态slot和跨调用复用保留；accepted/visible采用显式native-apply功能假设。默认逐批交付曾遇到控制交互和宿主限时限制；显式callback-batches后，指定四rank单层调用已经完成，不表示任意输入或千卡验收，见[公开范围](../examples/modern_deepep_native/README.md)。

transactions/5的可选`declared_service_fifo/1`为每个self连接调度独立start/apply/complete事件。它沿用源保护和目标NIC权限，释放由实际complete回调触发；不占本地GPU计算槽，也不创建网络载体或ACK。见[混合服务](../examples/scaleout_self_peer/README.md)。

`context_transactions.py`将显式communicator/source/context/peer身份编译成既有MRT1请求/响应载体，保留上下文隔离及声明的物理NIC；执行仍由同一个原生事务网络推进。核查入口对照实际包和连接生命周期，不新增provider。见[上下文绑定](../examples/scaleout_context_transactions/README.md)。

原生Scale-Out的可选`switch_links/1`通过同一Simulator关闭实际switch—switch两端，保留在途发送，区分本地排队/转发丢弃与物理到达丢弃。声明延迟后仅排除当时已知故障，重建同plane最短路/ECMP表，保留QP序号及拥塞状态。持久控制器监听网络状态变化，尚有配置事件时不能提前排空关闭；它不是分布式控制平面。范围见[物理链路失效](../examples/scaleout_link_failure/README.md)。

显式`live_ecmp/1`在上述机制上增加交换机本地候选过滤：实际关闭端口后，两端只安装原全局候选中的存活端口；下一次全局重建继续过滤本地坏端口，远端仍等待各故障自己的更新时间。不创建非等价绕路、不通知源、不迁移排队包；没有模拟额外本地检测/编程成本。配置见[本地失效端口剔除](../examples/scaleout_live_ecmp/README.md)。

v4/v5的可选`local_views/1`在物理存储和静态NIC注册之上维护应用子窗口；操作准入/实际完成之间持有本地使用，local节点完成才释放视图引用。最后引用为0只进入provider待处理状态，包接收不查询应用视图；发送方不读取目标关闭状态。见[窗口使用与关闭](../examples/scaleout_rdma_transactions/README.md#应用窗口关闭与底层注册分开)。

原生事务可显式分开物理存储和NIC注册权限：同一窗口承载一次实际字节，多个owner/NIC/rkey注册映射到子范围；接收载体检查实际端口匹配连接绑定，事务按该目标NIC解析权限。源读与本地copy继续使用存储key，跨别名重叠写和lease按物理范围保护。接口与范围见[多NIC共享存储](../examples/scaleout_rdma_transactions/README.md#多nic的权限共享同一份存储)；当前仅静态权限，尚无动态MR退出或provider成本。

transactions/4可选`bounded_copied_put/1`，将注册窗口、每源重传副本、目标请求缓存和本地copy快照分开限制。副本池不足时不捕获源数据，实际完整ACK清空副本并唤醒同源连接；默认失败和目标应用均不代替ACK。可选源隔离策略只在停止本地发包后归还复制副本，已构造原生包仍可产生旧远端写入。目标默认保留完整重放缓存；显式`carrier_once_put/1`依赖原生连续字节仅交付一次，只保留每连接单调序号并拒绝应用重放，不改变包或ACK。源读保护仍沿用事务完成条件。实现和边界见[有界缓冲](../examples/scaleout_rdma_transactions/README.md#有限副本与实际ack回收)、[隔离后回收](../examples/scaleout_rdma_transactions/README.md#失败连接停止发包后归还复制池)及[有序put](../examples/scaleout_rdma_transactions/README.md#有序put不保留完整请求缓存)。

transactions/5可选`submission_model.mode=explicit_prefix/1`将准备与发布分开：实际队列额度允许时产生prepared，非聚合操作发布同一连接的已准备前缀，源字节在发布时捕获。在线前端可等待真实prepared回调；ACK/原子回复完成后才归还额度，未发布尾部不自动补齐。它是MRT1功能规则，不是物理门铃或现代GIN成本，见[前缀发布](../examples/scaleout_rdma_transactions/README.md#准备工作后统一发布)。

transactions/5沿用v4事务/本地copy/在线反馈，只把显式零初始化的主窗口存储改为有界稀疏页。访问先按owner/key/逻辑范围和活跃使用记录检查，实际写先验证整段页容量，再修改字节；不引入额外仿真事件、页面时延或GPU行为。范围见[稀疏窗口](../examples/scaleout_rdma_transactions/README.md#使用大地址范围与稀疏初值)。

仅交互会话支持的 `staged_prefix/1` 将预约、描述符就绪与顺序ready更新进一步分开：Submit只预约；前端在已收到submitted后请求prepare，信用允许时产生descriptor_ready；连续就绪前缀才产生prepared。前端让已prepared的非聚合生产者发布，前缀使用max保持单调，较早生产者晚发布可以不增加网络工作。控制不占用serial_per_gpu lane，也不制造GPU准备成本；仍以原生网络ACK/回复释放资源。见[阶段示例](../examples/scaleout_staged_publication/README.md)。

原生`rdma-transaction-session`与Python`NativeTransactionSession`连接实际事务回调和动态前端提交：只在当前ns-3时刻交换命令，工作注册、应用依赖满足、发布、目标应用、请求ACK、原子回复和本地完成分别记录。物理空闲期间保留QP，显式结束才关闭资源。默认因果前端把完整程序留在Python侧，未来节点不预载后端；现代事件核心尚未接上此接口，self-peer和GPU可见性仍不支持。见[在线会话](../examples/scaleout_rdma_transactions/README.md#在线追加工作与实际回调)。

在线控制timer通过COMMIT的可选`alarms/cancel_alarms`在同一个原生Simulator安排或取消事件，真实到期后才给前端`alarm/fired`回调；不使用GPU FIFO、内存lease或另一个时钟，不等于网络/计算完成。工作与控制状态整批准入，显式结束还需控制timer已触发或取消。旧无timer路径不增加结果字段，见[控制定时器](../examples/scaleout_rdma_transactions/README.md#不占用gpu队列的控制定时器)。

transactions/4把本地显式copy接到实际窗口：入队保护源/目标，实际开始读源，完成事件写目标并释放，再解锁后继put。队列、源读、独占输出、事务和物理网络共同参与排空；错误不改成隐式等待，截止不补输出。见[本地输入输出](../examples/scaleout_rdma_transactions/README.md#本地处理显式读取输入和写出输出)。该能力只适用于声明copy的节点，不是现代事件/provider在线集成。

transactions/3在同一持久网络中串接“实际接收窗口→后续put”。源范围从提交到事务完成期间保留读使用记录，发布时才读取当前内存；普通写/原子/reset不能覆盖活跃范围。`Drained`要求没有残留读记录。该路径没有接入现代Python事件核心，本地处理阶段也尚未保护数值输入，见[源窗口](../examples/scaleout_rdma_transactions/README.md#将实际接收内容作为后续发送的数据)。

事务入口的transactions/2将实际窗口条件消费、本地串行处理和后续远端事务接在同一个原生ns-3时钟上；入队时消费、每GPU一个FIFO槽、实际完成才放行后继，本地尾部保留QP。它独立于stream-work/3和现代Python事件核心，处理耗时为声明值，见[事务本地阶段](../examples/scaleout_rdma_transactions/README.md#接收条件与本地处理交替执行)。

持久字节入口新增stream-work/3：实际网络接收/ACK回调可以解锁本地处理，本地处理在每GPU一个抽象串行槽上按声明耗时执行，再解锁后续写。全部使用同一原生ns-3时钟，处理期间QP与拥塞反馈保持；不与现代Python事件核心自动连通。具体事件和队列规则见[本地阶段](../examples/scaleout_rdma_streams/README.md#网络回调与本地处理交替执行)。

`rdma-causal-plan`将公开保存的前端wave图降低为stream-work/4：SEND对应一次write及真实ACK，RECV对应实际连续接收与其本地前驱的合取，COMP保留声明时长；组结束的零时长归并覆盖全部节点，随后才解锁该组下一步。它不经ASTRA再次调度，不保证与原一次性QP路径同纳秒排序等价，也不接入现代事件核心。输入hash及逐节点映射独立于线上报文，不用原节点ID改变端口或路径；实际连接绑定由显式stream配置决定。见[保存图的执行方式](../examples/scaleout_causal_waves/README.md)。

原生事务另支持`queue_fence`，在操作前驱满足时捕获提交GPU所拥有连接的已预留前缀，并按实际ACK/响应完成推进；后续工作不扩张旧目标。这是MRT1功能队列屏障，仍未绑定现代GIN flush或GPU源缓冲，见[前缀等待](../examples/scaleout_rdma_transactions/README.md#等待调用时已有的工作前缀)。

`RemoteTransactions`另支持提交GPU读取本地接收窗口的LE64条件等待与消费；值来自实际请求解码后的内存，全部条件满足才推进。它与远端应用记录使用同一网络时钟，但不提供GPU内存序或轮询成本；两轮例见[本地窗口等待](../examples/scaleout_rdma_transactions/README.md#根据实际收到的值推进下一轮)。

独立`RemoteTransactions`在持久载体上增加MRT1实际负载编码、目标窗口解析、put/fetch-add、返回值流及按事务完成回收的工作额度。原子请求ACK不释放其队列槽，返回值实际到达后才能推进；应用完成后仍等反向确认和物理排空。`remote_memory.hh`不访问源工作表，`remote_transactions.cc`负责网络接入与本地应用事件；公开入口为`rdma-transaction-probe`。它不是RoCE/GIN时延模型，实际事件核心的功能接入见上方modern_native，原事务示例见[远端事务示例](../examples/scaleout_rdma_transactions/README.md)。

独立网络增量`PersistentStreams`通过设备Receive/packet-generator回调使用同一原生Simulator，保留连接并按实际接收/ACK推进工作，公开入口为`rdma-stream-probe`。它已连接GinWorkQueue的单条工作资源状态，信用按完整ACK释放；现代Python事件核心尚未接到这里。原子事务、GPU可见与源buffer寿命待实现，不能作为现代DeepEP网络基线，见[持久字节传输](../examples/scaleout_rdma_streams/README.md)。

`modern_deepep_plan.py`通过独立公开命令编译固定现代direct的无时间操作部分顺序，保留Dispatch原子slot和由其决定的Combine通道公式；数据、计数和屏障的必要依赖与在未来运行时才可解析的顺序分别保存。离线plan本身不驱动队列；新增native接入的范围单独说明，离线使用见[现代计划](../examples/modern_deepep_partial_order/README.md)。

现代plan/manifest现为`/2`，将显式本地旁路、GIN self-peer和跨rank GIN分开。屏障和专家计数块的self-peer操作保留provider资源与完成义务；不能按src==dst直接跳过。旧清单拒绝并要求从保存输入重新编译，依据见[自发GIN更正](../fixtures/modern_self_gin/README.md)。

`modern_deepep_runtime.py`提供独立的`ModernDeepEPRuntime`事件核心与`modern-deepep-replay`。调用者逐项触发已满足依赖的事件，核心不自动完成操作；实际触发slot分配时才增加计数，取得同一owner的全部slot后建立Combine通道内顺序。它额外保留Dispatch同token全部目标slot原子分配后的warp同步。事件日志可以离线核验，独立replay不接C++队列或网络；新增native入口单独提供有界的实际回调接入。

运行时及窗口包装器的 `pending_dependencies()` 返回未完成事件和其未完成直接前驱的副本，包含上述动态顺序；空前驱只表示依赖满足，不能制造完成。`modern_native_wakeups.py` 的及时事件条件通过可选 `--callback-wakeups` 接入实际会话，256 次上限保持。原生端积累全部回调，订阅事件、闹钟或新排空才唤醒；该判别不新增仿真事件，缺省仍逐批交互。

互斥的 `--callback-batches` 使用提交条件的OR-of-AND（任一组全部回调发生）唤醒。原生仍执行全部Progress，保存实际非空收集批次及首批/新排空边界；容量阈值也强制交付，不插入新ns3事件。前端按原批次的推进时刻处理，中间批若需提交工作立即拒绝。256次上限明确计同步，另限16384逻辑批次；缓存硬界和记录见[使用说明](../examples/modern_deepep_native/README.md)。这种适配不改变网络恢复算法，也不提供真实GPU可见性。

`modern_deepep_windows.py`在事件核心外增加相对窗口解析、跨epoch源读保护、计数消费清零及滚动信号影子状态，公开入口为`modern-deepep-window-replay`。应用主窗口与provider索引信号分开，source local_complete与target visible分开；独立window-replay不编码或接收真实负载；新增native入口在外层接入实际负载/时钟，保留功能可见性限制。功能范围及宿主结构体核查见[窗口验收](../fixtures/modern_windows/README.md)。

`backend/rdma/gin_work_queue.hh`是独立的无时间资源核心，验证持久工作队列的槽复用、提交前缀和累计完成；尚未接入下述在线网络或现代应用计划。它的完成输入必须由未来provider回调提供，不能用作包时延、远端可见或现代库性能证据，详见[后端接口](../backend/README.md#gin工作队列功能核心)。

离线的`scaleout deepep-plan`由`deepep_plan.py`生成固定DeepEP低延迟路径的应用部分顺序，区分逐专家数据、零token通知、逻辑QP上下文和计算流事件。`deepep-check`检查保存表示并计算无时间依赖闭包；它不调用前端调度、ASTRA或网络，不改变下述在线服务。接口与尚未建模的队列、完成和缓冲语义见[操作计划示例](../examples/deepep_partial_order/README.md)。

非 UB Scale-Out 的可选 `recovery.mode=go_back_n` 在原生 NIC 协议回调上维护源端 ACK 进展计时，超时经原生发送调度重传，接收端重复数据用真实反向 ACK 响应。数据就绪、完整确认、QP 清理和软件额度释放保持分开；全部图完成后等待尾部网络流量排空。重试耗尽停止整个共享仿真，保留业务与资源状态供离线诊断，不返回正常完成。默认原生模式与依赖保持原行为；详细机制与参数见[恢复示例](../examples/scaleout_rdma_recovery/README.md)。

源端协议状态在`source_recovery.hh`中组合计时器和可选Karn RTT估计，不包含ns-3对象或评价真值；`rdma/recovery.cc`负责实际NIC/QP/事件接入，`recovery_records.hh`只保存共享上限内的记录。配置校验、独立RTT重放、协议审计和条件证书在Python分层实现。代码边界、确认缺失与源额度阻塞的关系见[推理通信脆弱性](communication_fragility.md)。

可选恢复历史由`recovery_history.hh`提供，每源独立缓存和LRU，目的地址区分目的端与plane；只在新QP首次实际发送时选择保守初值，更新只来自本源真实反馈采样或超时退避，不修改已运行QP。可选目的端份额在源总额度内限制所有plane到同一目的GPU的占用，目的端轮转跳过当前份额不足的队首。用户以[完整配置或条件清单](../examples/scaleout_rdma_resilience/README.md)使用；`scaleout_sweep.py`只组织公开操作和预算，不改变单例网络或前端调度。

原生Scale-Out可同时按活跃消息数、整条逻辑payload字节约束源端准入，单条消息过大时拒绝。发送协议进度来自源QP，仅用于离线导出；`queue_metrics`以每出口常量空间累计全程交换机队列积分，独立于详细包记录。`scaleout_metrics.py`离线分解生命周期阶段，不作为在线策略输入。配置与边界见[准入示例](../examples/scaleout_rdma_admission/README.md)。

非 UB 的 `service/1` 保留有限 MoE 片段；新增 `service/2` 支持完整 GQA MoE 结构尺寸、首尾/逐层图和权重＋KV 声明。`topology/2` 为同服务器不同 GPU 提供分块交换织构模型，按 GPU 入/出方向共享带宽；它与机外 RDMA 共用 Simulator，不冒称 NVLink 协议。`rdma/2` 显式声明执行限额，旧默认不扩容。Scale-Out 未缓存 KV 池按需创建元数据，保持原容量、FIFO 与引用计数；节点审计另存不滚动日志。配置、事件和边界见[完整形状百卡示例](../examples/scaleout_clos128/README.md)。两种服务 schema 默认使用合成路由，支持可选来源回放；均为声明成本、ready-KV Decode，不执行数值模型或声称硬件校准。下面的有限片段说明对应旧配置，UB 路径的结构保持独立。

非UB网络的物理输入生成由 `scaleout_topology.py` 独立负责，经公共 `scaleout topology` 导出带NIC/rail/plane映射的Clos/Agg拓扑。独立的 [RDMA 入口](../examples/scaleout_rdma/README.md)使用 `scaleout_service.py` 连接实际 LLMServingSim Scheduler、Chakra 图及 `backend/rdma/service.cc`；`astra_adapter.cc` 将 SEND/RECV 绑定到 `fabric.cc` 的原生 RDMA 网络。接收连续字节齐备后释放消费者，发送端完整 ACK 后释放发送；完成后返回前端再组批。多组、层、波次共享同一个原生 ns-3 实例，与下面的 UB 服务分别构建，不混链。来源选择经`scaleout_replay.py`校验和快照，图中保留文件/原层/入口身份，网络完成后推进入口；支持逐层等容量静态专家重排。具体范围及输入见[路由回放](../examples/scaleout_route_replay/README.md)，默认有限片段的[参数和输出](../examples/scaleout_rdma/README.md)单独维护。

可选[软件准入](../examples/scaleout_rdma_admission/README.md)只延迟已提交、尚未注入的跨机SEND，每源GPU共享额度；默认透传、FIFO或目的端轮转，决策成本显式计时。完整ACK与发送QP映射移除后才归还软件额度，再通知发送图节点；接收图节点独立按数据就绪推进。此逻辑不改变下一轮须排空本轮图的语义，不提供尚无硬件依据的缓冲提前复用。

moe-ub-cosim 将请求调度、模型任务生成和 UB 网络仿真连接起来。请求和专家选择决定需要执行的计算与通信；本次网络的实际完成事件决定后继任务何时开始，并影响下一轮组批。本文件说明这些模块如何协作。运行方法见[用户指南](user_guide.md)，通信接口见[后端参考](../backend/README.md)，模型近似和数据依据见[模型说明](model_validation.md)。

## 基本对象

**rank** 是一个逻辑执行成员，在部署表中映射到一个 UB 计算端点；它不表示宿主机上启动了一个操作系统进程。当前 CM384 场景将一个 rank 映射到一个 die，即芯片内的一个计算裸片。同一 rank 可以处理多个本地请求，也可以执行其他 rank 发来的专家计算。

**EP 组**是共同完成专家并行计算的一组 rank，成员必须显式声明。**批次**是一个 rank 本次选中的请求及其解码位置；不同请求的上下文长度可以不同。一个组的所有成员共同执行一次模型前向，在控制协议中称为一轮，字段名为 `wave_id`。无本地请求的成员仍可能承担远端专家工作。

**Dispatch** 将 Token 的中间表示送到专家所在 rank；**Combine** 将专家输出送回 Token 所属 rank 并合并。一个操作会展开成多个计算、发送和接收节点。专家选择记录决定使用哪些专家，通信模板决定贡献复制、消息字节、分块和等待关系，二者分别配置。

## 模块分工

| 模块 | 持有的状态与职责 | 主要代码 |
|---|---|---|
| Python 服务调度 | 请求到达、排队、批次、逐请求进度和 KV 容量；通过锁定的 LLMServingSim `Scheduler`、`Request`、`Batch` 与内存对象选批和记账 | [configured_scheduler.py](../python/moe_ub_cosim/configured_scheduler.py)、[full_model_service.py](../python/moe_ub_cosim/full_model_service.py) |
| 任务生成 | 根据本批次逐请求、逐层路由和静态专家放置，计算专家输入量、计算耗时及通信依赖，生成计划和 Chakra 图 | [plan.py](../python/moe_ub_cosim/plan.py)、[cann.py](../python/moe_ub_cosim/cann.py)、[full_model.py](../python/moe_ub_cosim/full_model.py) |
| C++ 服务驱动 | 校验提交、装载各 rank 的图、管理组的忙闲和完成通知；与 Python 交换 JSONL 控制消息 | [cosim_service.cc](../backend/cosim_service.cc) |
| ASTRA 执行层 | 按图的前驱关系和计算槽、发送槽启动节点，接收计算或通信完成回调 | 锁定的 ASTRA 依赖及[图装载代码](../backend/graph_sequence.cc) |
| UB 适配器 | 将一次 SEND 对应到一份 UB 事务，登记 RECV 等待，维护各自的完成回调；处理同芯片本地交付 | [ub_network_adapter.cc](../backend/ub_network_adapter.cc)、[ub_write_transfer.cc](../backend/ub_write_transfer.cc)、[local_delivery.cc](../backend/local_delivery.cc) |
| ns-3-UB | 在唯一事件队列中执行协议、分包、路由、排队、链路传输和流控 | 锁定的 `third_party/ns-3-ub` |
| 观测与导出 | 被动记录实际端侧操作和交换出口事件，转换本地时钟与采样窗口，另存操作和包字节的对应关系 | [port_observation.cc](../backend/port_observation.cc)、[bounded_export.py](../python/moe_ub_cosim/bounded_export.py) |

Python 和 C++ 是两个宿主进程；ASTRA 与 ns-3-UB 位于同一个 C++ 联合程序中。联合程序只链接 ns-3-UB 提供的一套 ns-3。`moe-ub` 命令包装上述已有流程，不增加另一套调度器或网络执行器。

## 一次选批到下一次选批

1. **接收当前状态。** 后端在初始时刻、外部请求到达或一轮完成时发送 `DECIDE`，给出绝对仿真时间、完成的图和可提交的 rank。普通数据包事件和单条传输完成直接在 C++ 内推进任务，不逐包唤醒 Python。
2. **更新请求并选批。** Python 先处理实际完成的请求进度和 KV 释放，再把已到达请求加入等待队列。只有空闲组能开始下一轮；忙碌组的新请求继续排队。组内各 rank 按先到先服务（FCFS）、序列上限、Token 预算和可用 KV 选出本轮请求。
3. **生成本轮图。** 前端按每个请求自己的解码位置读取专家选择，生成计算节点、通信任务和本地依赖。没有本地 Token 的 rank 仍生成需要承担的专家工作；完全没有工作的成员提交空图。
4. **校验并激活。** 前端用 `COMMIT` 交出图路径和下一次到达时刻。后端检查一组全部成员的计划、Chakra 文件、身份和资源映射，全部装载后才在当前仿真时刻激活。
5. **执行并等待实际完成。** ASTRA 启动前驱已满足且资源可用的任务。计算完成和 UB 网络事件继续释放后继节点；不同独立组共享同一网络，各自的队列和在途数据同时存在。
6. **报告整轮完成。** 该组全部必要节点、收发回调和本地交付完成，且结果已保存后，后端报告 `WAVE_DONE`。Python 据此推进各请求，释放结束请求的 KV，再选择该组下一轮。

这就是当前的连续批处理：批次成员可以在每轮边界变化。例如 A、B 正在执行，C 在此期间到达；该轮结束后，若 A 完成且容量允许，下一轮可以包含 B、C。当前不会在一张已激活图的中间插入 C，也不会因某个 rank 提前完成而让它单独进入下一轮。[连续批处理测试输入](../fixtures/m7_continuous_batching/README.md)给出了逐请求的批次与完成对照。

纯 Decode 中，本轮实际选中的每个请求恰好推进一个 Token；未选中的驻留请求不推进路由索引，也不释放 KV。每个请求完成只记账一次，不以批次总 Token 数代替逐请求更新。

多个层按同一 rank 的前后依赖连接。后层入口等待本 rank 前层的必要结果，远端等待由配对收发表示；不额外插入全 rank 的逐层屏障。一个组完成本轮后可以独立开始下一轮，无需等待其他组。多请求、多层和多组共同经过这条执行流程，组合验证见[测试输入](../fixtures/m8_composed_service/README.md)。

A3 局部资源模板为通信核分别分配计算资源，普通模型计算保留 `gpu_compute`。局部缓冲复用等待真实请求形成事件，目标数据就绪、源端协议回收与算子完成分别推进对应后继。连续层用算子完成节点连接，整张图仍须回收所有事务后才能报告完成；具体输入与事件映射见[后端参考](../backend/README.md#a3-局部资源模板)。

通信核静态分配还可作为原生 STORE 请求生产者身份。可选轮转策略只在一个 worker 的共享额度内选择下一个请求，不改变图、计算资源、核内同步、缓冲复用或完成事件；默认仍为统一 FIFO，详见[后端参考](../backend/README.md)。

## 容量与请求结束

每个 rank 使用一个逻辑键值缓存（KV）块池。接纳请求前按声明的最终长度或观察范围预留后续所需容量，已驻留但本轮未被选中的请求仍占 KV。暂时不足时请求等待，已有请求继续运行并释放容量；一个请求即使独占设备也无法容纳时，在启动前拒绝。当前没有隐式抢占、交换或重新计算来绕过容量限制。权重、工作区、通信缓冲和 KV 的核算范围见[模型说明](model_validation.md)。

请求有明确生成长度时，达到该长度才记录正常完成。真实路由文件只提供有限观察片段、真实结束位置未知时，片段结束记录 `OBSERVATION_ENDED` 并单独清理相应 KV；文件结束不能当成模型产生了 EOS。`service_finished` 表示声明的仿真工作负载已处理完，仍需后端确认图、事务和物理网络排空。

已有 P/D 测试通过独立 KV 交接图连接预填充和解码：目标容量先预留，`KV_DONE` 后释放源缓存并允许解码使用目标缓存。当前完整模型用户流程和首个 CM384/R1 参考场景采用 KV 已就绪的 Decode；P/D 的既有范围见[交接测试](../fixtures/m4_pd_handoff/README.md)和[交接排队测试](../fixtures/m4_pd_queue/README.md)。

## 图、部署与内部身份

执行计划记录要做什么，Chakra 图记录 ASTRA 要执行的节点与依赖。两者均不保存预测的通信完成时间。C++ 会独立重建并核对计划、消息字节和依赖，再逐项检查实际 Chakra 文件，避免只相信前端已经通过的校验。

规范化输入带 `schema_version`，不兼容版本和未知字段明确拒绝。全局时间与持续时间使用非负整数纳秒，本地时钟使用有符号整数纳秒；字节总量使用 64 位表示，进入较窄的后端接口前另行检查上界。修改文件名或文档版本不会自动升级输入 schema。

| 对象 | 作用 |
|---|---|
| `graph_id`、`wave_id` | 标识一次提交的图集合及其所属轮次；运行内不复用 |
| `rank_id`、`node_id` | 定位某 rank 图内的执行节点；局部节点号可以在后续图复用，回调必须同时核对图身份 |
| `operation_id` | 关联一次 Dispatch、Combine 或 KV 交接及其端侧记录 |
| `transfer_id`、`ub_task_id`、`comm_tag` | 分别用于内部传输、UB 任务和 ASTRA 收发匹配，显式建立对应关系 |
| `rank_to_ub_node` | 将逻辑 rank 映射到实际 UB 计算端点；不假定两种编号相等 |

专家放置可以逐层不同，但一次运行中保持固定。部署还声明 die、chip、server 归属。同 rank 的专家贡献保留为本地计算；同芯片不同 rank 的贡献使用本地准备和交付事件；跨芯片贡献才提交 UB 事务。同芯片交付仍满足依赖和容量约束，但不创建芯片内网络链路，也不产生 scale-up 网络数据包。

端侧额外准备成本按层和 rank 编入现有准备/打包 COMP 节点，由计算槽和实际依赖推进。物理出口服务区间在新包开始发送时决定其序列化速率，已发送包保留原完成事件；不按业务身份改变网络，也不改写历史完成时间。固定图重放可覆盖经校验的出口区间与原生喷洒开关；计算成本与操作类型变化仍需要重新构图，见[后端参考](../backend/README.md)。

身份只用于匹配、记录与核验。操作来源不会写成新增业务包头或标签，不参与优先级、路径选择或固定延迟配置。改变批次、路由、专家放置、模型结构或通信模板时，必须重新生成相应计划。

## 唯一的执行时钟与两种通信完成

所有计算和网络事件使用 `ns3::Simulator` 的绝对纳秒时钟。计算耗时通过 `sim_schedule()` 登记一次，网络等待来自本次 UB 事件。前端收到 `now_ns` 后直接更新当前时间，不叠加另一个模拟器给出的批次耗时。等待 Python 构图、文件写入或控制消息的宿主耗时不计入仿真时间；这些开销另行计入运行资源测量。

联合路径包括 `URMA_WRITE/RTP` 和原生 STORE/LOAD。WRITE 与 STORE 在目标累计收到完整有效数据后产生 `data_ready`，完成消费者 RECV；源端确认和资源清理后记录 `transaction_done`，异步完成 SEND、释放发送槽。两种时刻分别记录，接收计算不被强制推迟到源端 ACK 返回。数据先到而 RECV 后登记时，在登记当时异步完成接收，不回溯时间。

原生 LOAD 由消费者发起：图中先执行供数方的真实元数据 STORE 通知，消费者收到通知后发起读取，并在完整响应返回后继续计算。图中 SEND 表示请求发起，LOAD 的数据方向与之相反；数据拥有者、消费者和发起者分别记录。原生路径经过同一网络的选路、VOQ 和物理端口，不依赖 RTP TP/Jetty。完整依赖、字节范围与资源回收条件见[后端参考](../backend/README.md)。

计算耗时不再作为 UB 任务延迟重复增加；通信模板已经展开的发送不交给 ASTRA 再展开一次集合通信；联合任务也不再由 `UbTrafficGen` 的 CSV 阶段调度。计算 profile 已包含的内存访问不重复计时。

## 观测怎样产生

可选的端侧发送资源记录从 ASTRA 真实调度队列及发送资源集合读取待发数、占用数、容量。它不依赖事后节点身份关联；状态跨图连续，导出时才转换为 rank 本地时间。采集默认关闭，显式开启后受条数和存储上限约束，见[发送状态小例](../examples/send_state/README.md)。

端侧记录来自实际执行节点和完成回调，表示某 rank 的 Dispatch、Combine 等操作何时开始、何时结束。交换机记录来自出口成功开始发送的事件，按 `Packet::GetSize()` 计入整包字节。观测模块同时维护包字节到业务操作的内部对应，用于评价关联算法，且不会改变包或执行时序。

新增操作阶段分别记录真实准备节点、逻辑任务提交、消费者数据就绪与发起资源完成，保留原端侧区间含义。VOQ 等待量与出口 FIFO 等待量在实际变化时被动累计面积和峰值；VOQ 排除入口处理中数据，两种队列都排除正在发送的包。每个交换机只订阅一次 VOQ 通知，再分发到所选端口。

设备时钟只用于被动统计和导出，不参与执行调度。详细事件可在导出时重新分窗；原窗口的队列统计在执行过程中直接累计，并与状态历史重算结果核对。算法输入保存在 `observed/`；全局身份、时钟参数及操作与业务字节的对应关系保存在 `truth/`。窗口、单位、字段、稀疏零值和读取方法由[记录与存储格式](record_storage.md)统一定义。

执行时可直接保存本地窗口汇总，并仅为选定端口、区间另存详细记录。分类真值分别保留实际业务与应用状态消息的来源；原生数据包到达最终端点后即可释放其被动来源范围，协议自己的在途状态保持。

重复实验可以采用两种流程。`sample` 根据保存类型对齐合并基础窗口，或读取详细发包和队列状态重新分窗，不执行网络；资料不足在创建输出前拒绝。`replay` 装载已保存的完整首轮计算和通信图，重新执行 ASTRA/ns-3-UB。后者仍由本次完成事件推进，不按历史包时间定时发送。首轮数据包的范围、已支持的参数调整和目标场景见[用户指南](user_guide.md)与[CM384/R1 场景](scenarios/cm384_r1.md)。

## 设计依据与验证入口

原 v0.2/v0.3 方案及其章节与现行说明的对应关系集中在[设计档案](archive/README.md)。功能验证与目标规模验证保存在各测试输入目录及[历史索引](milestone_history.md)。小场景通过不自动证明目标硬件的耗时精度，精度与来源状态由[模型说明](model_validation.md)记录。

## 有限层段执行

公开准备入口从完整模型服务输入选择一个 Decode 步骤和连续原始 MoE 块，容量仍覆盖完整部署。前端将所有声明请求提交为初始批次，各层依赖在同一次 ASTRA/ns-3 执行中自然推进；空网络从 0 ns 开始，首块输入与该步骤 KV 声明已就绪。块内相关计算保留，模型前后处理与采样不执行。完成后的结果和保存包明确标记有限范围，不能作为完整生成 Token 或任意中途快照。接口见[用户指南](user_guide.md)，事件与记录见[输出数据](record_storage.md)。

持久传输的可选恢复与工作提交共享原有ns-3时钟。应用发布扩展合法字节范围，实际TX才启动源计时，真实ACK/NACK推进信用；完整重复数据仅补发ACK，不再次向事务解析器交付。连接空闲后可继续追加，排空与显式关闭分别检查。当前行为与输入见[持久恢复](../examples/scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)，不涉及连接重建或现代GPU可见性。
