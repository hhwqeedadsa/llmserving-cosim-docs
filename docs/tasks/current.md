# 当前任务与交接

状态：`in_progress`。当前目标是按A类网络、系统或仿真论文标准评估和扎实现有研究；最佳论文是质量追求，不可保证获奖。上一阶段的平台可交付结论不能作为本目标完成证据。本阶段从干净的`bf57681`继续，仅本地提交，不push。

## 目标与验收

- 以现有代码、原始结果和一手相关工作审计问题价值、创新性、真实语义、强基线、泛化、校准、规模和可复现性。
- 不把目录重构、钩叉功能数量、小场景测试通过、热点直方图或经典RTO/LRU组合当作独立论文贡献。
- 优先检验当前异常是否依赖未经校准的软件源额度；通过公开入口完成独立的字节/槽/透传敏感性对照，保留无收益、反例和失败。
- 建立带证据状态的论文主张清单、近邻工作逐项差异、可证伪主问题和强基线；网络、系统、仿真三条路线按证据选择，不同时堆功能。
- 明确真实测量、通信库语义、较大代表性负载与留出验证的缺项及取得方法。未有这些证据时不宣称已达到A类或最佳论文级别。
- 本轮评估与筛选是目标的一部分；论文主贡献未成立时保持研究目标进行中，不以一份评估报告替代整个目标。

## 当前范围和预算

不改固定依赖、不安装、不下载模型或大型数据；复用已校验的一手资料和本机原始数据。必要公开页面只读请求设每页2MiB、总16MiB、单请求20秒、总120秒，失败保留。新研究放`experiments/paper_readiness/`，结果放`/dev/shm/moe-paper-readiness-20261022/`。首个判别研究16条件（2 batch×4额度模式×2故障），各条件60秒/RSS1GiB/地址2GiB/仿真5ms；每次sweep300秒，累计产物128MiB，卷预留8GiB。没有硬件校准含义，不因结果不理想抬限或制造热点。无新现象不自动扩矩阵。

## 当前进展与前阶段

已核对：恢复研究24条件主要为8GPU合成层段，正常初值下历史无业务收益；静态目的端份额有负结果。四份真实R1路由已取得并进入4卡原生网络，但不是训练分布或总体代表样本。历史512卡完整参考执行成功，1024卡相同长工作量两版本均600秒超时，不能声称千卡完整推理已验证。现有源额度明确为软件控制，不是实测NIC或GPU缓冲。

本轮16条件已完成并独立诊断，来源/共同图/首包路径核对通过。去字节上限后聚合完成不变，但batch4有5条消息记录变化，40/41交换准入；放宽并发/透传后软件准入等待为0，确认丢失仍有明显业务影响。不能把聚合相等说成执行相等，更不能把放宽未校准约束当成算法收益。初次分析过强等价断言失败后已如实更正并保留，未重跑仿真。

已复核15份近邻一手快照hash，并读取RFC6298、RFC9040、NSDI/SIGCOMM官方征稿。12项主张登记为有范围支持、缺失或被反例削弱；不提供主观百分制评分。源码发现每逻辑消息新建并清理QP，而固定DeepEP示例复用Buffer且按本地专家数配置QP，其等价性未证。这可能影响短QP零样本与步间等待，是下一步G1优先核查项。

评估与实证路线见[报告HTML](../review_artifacts/paper_readiness_20261022/report.html)、[16条件判别](../review_artifacts/paper_readiness_20261022/screening.html)、[语义疑点](../../experiments/paper_readiness/api_semantics.md)。16份公开重新诊断通过，分析确定性重复、来源/主张hash与HTML链接核对完成；检查点归档约1.10MiB、1157文件校验。它只证明审计与筛选检查点，不证明论文目标完成。具体见[verification](../review_artifacts/paper_readiness_20261022/verification.json)与[archive](../review_artifacts/paper_readiness_20261022/archive.json)。

后续G1静态核查已形成实质结果：取得固定DeepEP v1.2.1七份源码，复用两份固定缓存，共九份与同一完整Git树/blob/hash核对。确认按peer/expert访问已有RC池、逐贡献数据put、包括零token的count/flag、双buffer和CUDA事件。当前rank聚合/逐消息建QP与该路径不等价，C12据此收紧；不声称测得真实NIC恢复/RTT状态，也没有修改后端或锁定依赖。模型依据中“真实原件仍未取得”的过时说明已纠正。

四份真实路由的7,424个四请求/层窗口离线核算完成。声明四rank、batch1、关闭跨rank P2P时，单阶段非自身count为5,701,632次，其中96.90%对应零token；不是同数包或线上字节。既有两层/两步公开输出的76条消息逐条匹配抽象数据量，但不包含6,144次目标库count/flag操作。新证据见[G1报告HTML](../review_artifacts/deepep_semantics_20261022/report.html)、[精确源码与核算](../review_artifacts/deepep_semantics_20261022/audit.json)、[独立检查](../review_artifacts/deepep_semantics_20261022/verification.json)。独立枚举7,602,176个source/expert项、25份摘录及两份wave hash；公开input/wave迁移后离线重复逐字节一致，没有执行新网络或测量硬件。前两次定位器未区分重复API说明而失败，原因保留。

平台增量：已实现公开`scaleout deepep-plan/deepep-check`，输出固定v1.2.1计算流等待、无hook、单NIC/RDMA-only模式的离线操作部分顺序，分开数据/count/flag、逻辑peer/expert上下文、目标可见与发送完成，保留默认流和双buffer的依赖。没有运行GPU、创建物理QP或接入ns-3；源buffer重传寿命、CQ/WQE资源和时延仍未建模。人工四rank新目录流程执行8条公开命令，728事件/160操作，含未知事件与篡改拒绝、迁移核对；单元反例与适用范围见[操作计划示例](../../examples/deepep_partial_order/README.md)。

真实路由操作研究已完成：平台固定`0cc140f`，六条公开CLI将四原件的两层/两步选择编译为8,448操作/33,968事件。独立枚举全部操作、768个逻辑上下文；逆向后继闭包核对32个设备完成节点。暂扣零count/Combine flag分别阻塞29/25个完成；sender_done不在本应用直接等待中，不能据此排除NIC资源反压。三个重复产物逐字节一致，最大子进程RSS94,572KiB。见[报告HTML](../review_artifacts/deepep_operations_20261022/report.html)、[独立检查](../review_artifacts/deepep_operations_20261022/verification.json)与[本地检查点](../review_artifacts/deepep_operations_20261022/archive.json)。这是离线部分顺序，不是包级执行或硬件时延。

新源码边界：固定v1 nonfetch通知实际构造masked原子WQE，不能当4B普通流；当前原生适配入口仅传消息字节。固定现代README blob说明V2/V2.5已转NCCL Gin并移除V1/NVSHMEM，但未核对现代内核，不能把旧版补模作为现代强基线或论文创新。

现代候选已固定`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`，12份源码/blob/hash、18摘录核对。真实16条选择下，direct Dispatch按目的rank去重，91个跨rank专家贡献变成38份token put；计数是48个rank值+48个专家块put，不能沿用旧版3,072次原子通知推断瓶颈或创新。现代QP索引按SM/通道共享，direct核心Dispatch/Combine确有入口/出口屏障；关闭hybrid时逻辑scaleout=1也可能有物理跨节点RDMA。FULL/RAIL连接约束影响multi-rail基线可部署性。见[现代源码与后端改法](../review_artifacts/deepep_modern_20261022/report.html)及[独立选择核算](../review_artifacts/deepep_modern_20261022/accounting.json)。没有执行现代库、NCCL provider或新网络，源码要求Hopper+而当前硬件匹配未确认。

固定NCCL v2.32.3-1/`12df1a1`的18份GIN/GDAKI源码已核对。确认上下文索引按连接数分解、连接绑定本地设备、peer QP持久存在、CQ反馈限制槽复用；flush本地源复用与远端可见分开，AggregateRequests跳过doorbell而非合并操作。2,209次映射算术核查及全路由7,424窗口/118,784矩阵项核算完成：逐专家176,567份跨rank贡献经modern direct去重变为69,409份token，目的rank入量最大/均值p95从2.074降为1.5，仍非均匀。这是条件操作数核算，没有时延或包执行。见[源码/路由报告](../review_artifacts/nccl_gin_20261022/report.html)、[实施规范](../../experiments/paper_readiness/modern_backend_contract.md)及[重复/迁移复核](../review_artifacts/nccl_gin_20261022/followup.json)。

平台独立资源核心增量完成：`GinWorkQueue`分离reserve/prepare/doorbell/consume/CQ与flush前缀，允许空闲后追加及一个应用操作拆为多个work，声明式上下文映射区分连接与本地设备。严格C++17编译通过；三工作/两槽的独立逐工作oracle枚举49个合法交错状态，另检查1,024次追加，共3,241项断言，峰值子进程RSS107,744KiB。见[范围与验收](../../fixtures/gin_work_queue/README.md)。这是单槽条目的无时间核心，未接入旧字节消息后端；多槽reservation、原子事务、远端可见、源buffer和网络代价仍缺，不能宣称现代DeepEP或在线持久QP已实现。

现代布局与并发槽位核查完成：补读6份固定源码，联合复核11份，320种布局独立整数核算通过。H7168/Top8/BF16的Dispatch/本地归约Combine put分别14,464/14,400B；前期hidden-only数字继续保留其范围。发现Dispatch的atomic slot分配会影响Combine接收序号、通道及GIN连接，不能预先按token序号写死。见[布局与并发槽位](../reviews/现代通信布局与并发槽位_20261022.md)。没有新网络或硬件执行。

平台现代操作输入增量完成：公开`scaleout modern-deepep-plan/modern-deepep-check`，独立schema表达fresh direct/BF16/expanded本地归约、完整数据布局、rank/专家块计数、滚动屏障、覆盖旧信号尾部的flush、共享buffer epoch和epilogue。Dispatch slot保持符号变量，Combine通道按实际slot解析；动态双射和通道顺序仍是未执行约束。16项单元检查通过；人工四rank新cwd的12条CLI检查1,076事件/224操作/16符号slot，含4类拒绝，重复三产物及迁移check一致。见[接口与范围](../../examples/modern_deepep_partial_order/README.md)、[验收](../../fixtures/modern_deepep_plan/README.md)。没有网络/GPU执行，不能视为现代在线库已实现。

现代真实路由研究完成：平台固定`fc5de83`，四原件entry1/2、层3/4经7条公开命令生成486操作/2,334事件，独立枚举全部操作并回查16条原始选择。远端Dispatch/Combine各38份；在声明两连接/2SM配置中前者全走连接1，后者连接0/1为23/15，不能当利用率或性能。5个暂扣探针由独立后继枚举核对；三份重复产物一致，41文件本地检查点hash通过，最大子进程RSS32,436KiB。见[现代真实路由报告](../review_artifacts/modern_operations_20261022/report.html)。本选择每source/destination至多一个token，slot无组内歧义；一般batch仍需动态解析。

平台事件核心已实现：公开`ModernDeepEPRuntime`和`scaleout modern-deepep-replay`按调用者逐项触发的事件分配slot、解析Combine通道并执行通道内接收序号顺序，不自动制造完成。源码复核另补Dispatch同token所有目标slot分配后才允许put的warp同步，旧符号DAG闭包不保证这项执行条件。23项相关单元检查通过；新cwd的10条公开CLI核验1,076事件/16分配/16个Combine绑定，4类拒绝、重复与迁移逐字节一致。见[事件核心范围和验收](../../fixtures/modern_deepep_runtime/README.md)。它没有执行队列、provider或网络，测试提供的完成不构成研究观测。

平台队列增量完成：`ReserveBatch`按固定provider正常availability路径一次预留多个连续WQE，等整段末尾槽可复用后才允许写入，发布不得截断reservation。深度4、区间2/1/2的独立旧槽owner枚举122状态，保留旧49状态和1,024条追加回归，共4,976项断言，严格C++17编译通过。显式排除free-flow及skip-availability，单区间≤depth是支持限制，未推断小token需要多条、包数或时延。见[队列验收](../../fixtures/gin_work_queue/README.md)。未接网络。

平台持久原生字节路径完成：`PersistentStreams`与公开`rdma-stream-probe`不改固定依赖，通过设备回调保留QP对象/序号/拥塞状态，在同一Simulator按真实接收/ACK推进工作。初始可发送字节为0，后续只有实际发布才增加前缀；显式绑定唯一源/反向NIC，GinWorkQueue按完整ACK释放。7条公开CLI覆盖深度1/2、同连接同时发布、重复和两类拒绝；独立枚举31,457B对应全部35个实际源包，序号/工作边界/NIC/因果核对通过。见[入口与实包验收](../../fixtures/persistent_streams/README.md)。这是人工字节功能小例，不是现代GIN或生产MoE；ACK信用不是实测CQ/源buffer，未声称完成时间变化是优化收益。

平台远端事务功能增量完成：公开`rdma-transaction-probe`通过实际MRT1负载传递地址/key/put字节/原子增量，目标解码后执行窗口，原子旧值实际经反向持久连接返回；独立应用队列等请求ACK和返回值再释放。8条新cwd公开CLI核对6操作、10包/416B、跨包12包/545B、并发根工作、id改名不扰动线上执行、重复与两类拒绝；456项内存核心断言、8项输入检查及旧持久流程回归通过。来源区分固定GIN原子WQE、Linux RXE应答/重放语义与本地功能选择，见[范围和验收](../../fixtures/remote_transactions/README.md)。没有丢包重试、GPU可见或现代事件接入；MRT1/反向普通流明确不等价RoCE/Atomic ACK，不是现代性能基线或研究收益。

窗口接入核查发现平台语义缺陷并已修正：现代屏障signal和非NVLink专家计数块发给自己仍走GIN/self-peer QP；旧plan/1将其全当本地旁路。新plan/manifest/2新增gin_self分类、保留专家块发送区并明确拒绝旧清单。人工两call四rank的40项self GIN经源码链和独立分类核对，15项单元检查、15条计划流程及10条事件回归通过，见[更正与验收](../../fixtures/modern_self_gin/README.md)。没有新网络或硬件执行；自发GIN的物理回环路径和成本未定义，不能补为零或跨交换机流量。

真实路由self-peer更正已独立复核：平台`c08cbb9`，9条公开CLI生成486操作/2,334事件，其中364跨rank GIN、80 self-peer GIN、42本地旁路。80/444约18%仅为API请求比例，不是线上字节或耗时。对照已核hash的历史归档，80项path/verb更正、64项专家块新增发送区，完整DAG/slot/layout及跨rank结果保持不变。7个暂扣探针、四原件16条选择和三份重复产物核对通过；新检查点约651KiB。见[更正报告HTML](../review_artifacts/modern_self_gin_20261022/report.html)、[独立复核](../review_artifacts/modern_self_gin_20261022/verification.json)与[维护记录](../review_artifacts/modern_self_gin_20261022/maintenance.json)。没有执行网络、硬件或性能对照；旧报告保留其固定版本。

现代窗口增量已实现：公开`modern-deepep-window-replay`将已解析操作映射到共享应用注册区和独立provider信号域，检查源读lease、跨epoch写覆盖、计数消费清零与滚动signal/shadow。12份固定源码身份及原始结构体宿主ABI核对，workspace结构15,831,168B/分配16MiB；不是CUDA构建或显存测量。24项相关单元检查、新cwd的12条CLI/5次拒绝及旧计划/事件回归通过，详见[窗口范围与验收](../../fixtures/modern_windows/README.md)。影子状态没有接收真实负载，token数值、GPU内存序与完整buffer寿命仍未验证，不构成现代性能基线。

当前下一步：将现代窗口及事件与真实传输负载和provider回调相连，先明确self-peer GIN的资源关系和不支持边界。provider报文、原子并发额度、响应调度和可见性都须有范围依据；MRT1仅用于功能验证，不能包装成现代GIN成本。设备信息仍待原询问回复，不假定可用硬件。基线语义与成本可验证后再对比正常无故障真实路由排队和方法排序。四请求不能复制为千卡生产样本，1024卡长工作量完整执行仍未完成；留出数据、强基线、匹配校准与规模泛化仍缺失，论文目标保持in_progress。

上一阶段平台`52e1afb`、研究`bfa2e7d`、收尾`bf57681`，证据见[前阶段核对](../review_artifacts/inference_resilience_20261022/completion_audit.md)。旧阶段记录与原始产物保留，不修改为新目标已经完成。
