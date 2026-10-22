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

本轮拓扑感知近邻与分层EPLB基线完成，论文目标仍in_progress：取得TA-MoE官方正文/补充及固定gate，确认其训练辅助损失改变路由，不能直接复现于冻结R1；ExFlow只取得固定v2作者摘要，PDF超2MiB上限curl63/0B，不换源或抬限重试，具体Top-k/执行仍未知。DeepSeek官方EPLB固定`d52c72d`已明确按逻辑组装箱到节点，保组不是新思想。对EP4/四节点各一个活动GPU/E256/零额外副本做标准库计数适配，不执行官方代码；显式组ID平局与torch未规定稳定排序不同，全/尾Prefill有6/24层出现组权重相等。五条件完整核算：连续69,409份/p95计算输入15，普通全/尾LPT80,596/79,515份/p95均14，分层全/尾适配70,123/69,469份/p95均15。已有分层思想回收多数通信增加，未同时保住LPT计算指标，不能称新算法或时延收益。独立回查四原件、37,120窗口/593,920矩阵项/148,480选择、928次贪心决策及commit→tree→blob；仅observed迁移不同cwd重复两产物字节一致。144文件归档逐hash通过，约2.24MiB；13依赖检查通过。每响应2MiB/20秒，六采集阶段累计小于16MiB；每命令60秒/RSS1GiB/地址2GiB、单阶段32MiB、新产物64MiB/累计128MiB、预留8GiB。无新网络/GPU/构建/安装/依赖变更。见[报告HTML](../review_artifacts/eplb_groups_20261022/report.html)、[独立核验](../review_artifacts/eplb_groups_20261022/verification.json)、[来源](../review_artifacts/eplb_groups_20261022/sources.json)、[归档](../review_artifacts/eplb_groups_20261022/archive.json)与[协议](../../experiments/paper_readiness/eplb_group_protocol.md)。

本轮分组路由审计完成，论文目标仍in_progress：四请求原件29,696条选择均满足参考8逻辑组/最多4组条件，旧无约束交换却有23,450—23,717条超限；历史+14.44%—14.68%副本增幅不能外推为模型内变化。固定三旧种子/每cell4096尝试补cap4与within32共六条件：cap4为68,651—68,733份，比原始69,409下降0.974%—1.092%；within32具体专家集合几乎全改而完整窗口矩阵均不变。仅保留必要条件，不称可执行模型或优化；额外拒绝改变有限交换轨迹，不能归因于单因素。独立核对14条件/103,936窗口/1,662,976矩阵项/2,088边际单元、89,088条within32组计数；换cwd重复12份非日志产物字节一致。连续放置464个逻辑组均同址，全/尾Prefill LPT有464/460组横跨四rank，为此前通信增加提供结构解释而非隔离因果。取得并读取MoETuner固定v1，确认两阶段负载/带宽放置已有，跨层路由目标与当前层内去重不能混同；尚未复现其优化器。Patterns已有权衡解释；本机旧Vidur配置只作参考，HF元数据20秒超时0B、历史collector仍未知，不重试。修正新请求采集器递归subject目录查询及同名拒绝，未远端执行，原失败源码保留。无新GPU/ns-3/构建/安装/依赖更改；每命令60秒/1GiB/地址2GiB、单阶段32MiB、新产物64MiB/累计128MiB、预留8GiB。103文件本地检查点逐文件hash通过，约7.04MiB；见[报告HTML](../review_artifacts/routing_constraints_20261022/report.html)、[独立核查](../review_artifacts/routing_constraints_20261022/verification.json)、[来源](../review_artifacts/routing_constraints_20261022/sources.json)、[归档](../review_artifacts/routing_constraints_20261022/archive.json)和[方法/复现](../../experiments/paper_readiness/README.md)。

本轮固定分组映射强基线已完成：3分组×identity/最少远端/最小出口服务量共9条件，Prefill来源归一化选择24排列，未调Decode或执行新网络。两种LPT最大副本改善仅0.318%/0.250%，所有条件出口服务量p95不变；计算最大输入逐窗口不变。独立核对4,176候选、174层/分组、66,816窗口/1,069,056矩阵项，observed新目录重复计划字节一致。追加结果后覆盖下界：全量/尾部LPT即使每token理想本地旁路仍≥77,752/76,393份，高于连续实际69,409；未来全知固定排列为79,354/78,228，用174次DP和89,088个原始token覆盖交叉核验。仅换rank不能消除该分组的通信差距，收缩此创新候选，不把已知排列/覆盖关系当论文贡献。新请求目录连接失败0B，无独立留出；初版socket20秒实际请求40.07秒，整采集受60秒监控，已保存失败脚本并改整请求子进程deadline，未重试。每研究命令60秒/1GiB/地址2GiB，单阶段32MiB、新产物64MiB/累计128MiB、预留8GiB；无新GPU/ns-3/构建/依赖变更。见[HTML报告](../review_artifacts/mapping_baseline_20261022/report.html)、[九条件核查](../review_artifacts/mapping_baseline_20261022/verification.json)、[下界核查](../review_artifacts/mapping_baseline_20261022/bound-verification.json)和[协议](../../experiments/paper_readiness/mapping_baseline_protocol.md)。论文目标仍in_progress。

本轮Dispatch数据研究已取得并独立核对结果：固定层3/Decode1—4、EP4、同一Clos的全100G/上层25G、三放置共6条件+1重复。连续/全Prefill/尾部Prefill为35/44/43份；全100G的ACK完成63.309/67.807/66.692μs，上层25G为107.015/126.001/121.465μs。位置2两个LPT同为11份却分别27.936/32.804μs，实际Agg10→Core16负载45,732/60,736B；总量不能替代路径。独立检查7,320条数据/ACK路径、39,360次发送、32,040次ECMP选择、192出口积分及244工作回调，24轮完成分解无残余。无丢包/ECN/PFC；数据出队后峰值32,580B低于ECN阈值。固定result和四份分析重复字节一致；首次检查器变量遮蔽错误在读取前失败，日志保留，未重跑网络。复用原生后端、不改依赖/构建/联网/GPU，运行器11.04秒/采样RSS416.58MiB，明确60秒/1GiB/2GiB/5ms/64MiB预算，新增累计128MiB、预留8GiB。只执行跨rank数据，四轮由实际ACK串行推进；不是现代完整推理、硬件校准、千卡或新算法收益。见[报告HTML](../review_artifacts/dispatch_network_20261022/report.html)、[核验](../review_artifacts/dispatch_network_20261022/verification.json)、[协议](../../experiments/paper_readiness/dispatch_network_protocol.md)与[复现](../../experiments/paper_readiness/README.md)。论文目标仍in_progress。

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

实机校准条件已作只读核查：当前进程可见6张A100 80GB/SM8.0，无Hopper候选；sysfs所见4个mlx5端口Down/Disabled，verbs枚举0 HCA且无uverbs节点。当前PATH无nvcc，两个明确选择的Python无torch/NCCL分发元数据。没有创建GPU/RDMA上下文、发送流量或改变配置。固定现代README要求Hopper+及对应CUDA/PyTorch/NCCL；已有A100测量不能冒充该分支校准。报告与后续判别实验见[校准条件HTML](../review_artifacts/calibration_readiness_20261022/report.html)。环境只读脚本可独立重用，仍不证明性能或论文贡献。

本地接收窗口等待增量完成：`rdma-transaction-probe`支持提交GPU的LE64条件与可选消费清零，由实际收到的MRT1字节驱动下一轮。新cwd的8条CLI核对两轮10事务/14源包/560B及6个本地条件；少计数/错数据均保持7个后续操作等待且不清零、不偷发。独立实际包解码和内存重放、重复/id改名不扰动、6项输入检查/479项内存断言、旧8条事务CLI回归通过。增量构建只用固定原生ns-3；没有现代DeepEP/GPU/GIN执行，仍属功能模型。详见[范围与验收](../../fixtures/local_memory_waits/README.md)。

队列前缀快照增量完成：原生`queue_fence`只捕获source-owned已预留工作，并由真实MRT1 ACK/返回进度结束；未来工作不扩大快照。新cwd的8条CLI核对6事务/52源包/47,960B，覆盖旧快照排除后续工作、深度1预留未发布工作、空快照、重复/id改名及2类拒绝；原事务与本地窗口16条CLI回归、7项输入检查通过。功能队列屏障不是GIN flush/GPU源复用，也未连接现代完整应用；来源与范围见[验收](../../fixtures/queue_fences/README.md)。

真实路由8条件研究已独立核验：保持每请求/层专家边际热度的3组共选交换，modern direct跨rank Dispatch由69,409份增至79,432—79,600份（+14.44%—14.68%），专家贡献矩阵完全相同；累计去重矩阵相同的相位对照仍改变7,239/7,424个窗口矩阵。比值更均衡不代表绝对需求下降：8条件最大入量p50/p95都为3份，随机对照总量反而增加。独立枚举59,392窗口/950,272矩阵项，检查696个度数守恒单元、四原件和重复迁移结果；80文件检查点hash通过，归档约4.42MiB。见[报告HTML](../review_artifacts/route_controls_20261022/report.html)、[独立核验](../review_artifacts/route_controls_20261022/verification.json)与[复现命令](../../experiments/paper_readiness/README.md)。本轮是离线输入保真度的机制判别，不是训练或生产分布、包级执行、时延收益或新算法；不执行新网络/GPU，不改依赖。目标仍in_progress。

Prefill放置强基线已核验：Patterns behind Chaos固定v5已有热点/共激活洞察及Prefill放置，不能作为本项目创新；其公开artifact固定`b059b85`的四份源码按tree/blob/hash复核，6次只读响应1,172,179B，无下载代码执行。按公开计数LPT适配EP4，规划只读Prefill、评价才读Decode；全量/最后最多10个token两种放置的计算最大输入p95均15→14，Dispatch副本69,409→80,596/79,515（+16.12%/+14.56%），不能换算时延净收益。独立核对22,272窗口/356,352矩阵项、174放置层及迁移重复。另以8选专家代数反例证明单专家与全部专家对统计相同仍可使P/Q放置通信量排序相反；不是生产发生率或新超图算法。第一次反例检查的奇偶预期标签写反已手算更正，保留失败且不重跑真实基线。86文件归档约1.51MiB，见[报告HTML](../review_artifacts/prefill_placement_20261022/report.html)、[源码与原文](../review_artifacts/prefill_placement_20261022/sources.json)、[核验](../review_artifacts/prefill_placement_20261022/verification.json)。本轮无GPU/ns-3、构建或依赖变更；论文目标仍in_progress。

当前下一步：固定映射、路由审计与五条件分层基线均已收尾，不按结果自动扩条件。已知EPLB分层方法基本消除LPT通信增加，先核算固定整组/容量下相对这些基线究竟还有多大空间，再决定是否需要新分组方法；有限贪心结果不是理论不可改善证明。继续取得独立请求留出并核对适配输入/更新频率、冗余副本资源和更强已有方法；不能把跨层专家对直接当层内共选或将正文公式擅自补成原版复现。之后才预注册GPU对带宽、共享物理端口服务量、实际包级完成的排序判别，检验差异在留出请求/ECMP/上层带宽中是否稳定且有意义；已有方法解释或消除收益时正常收缩，不重发明保组装箱。rail/plane、拓扑和新请求需独立预注册泛化，现有stream入口16连接/64工作须作为平台任务先验收。完整现代性能对照前仍须把窗口/事件与真实传输和provider反馈相连，明确self-peer GIN资源及报文/原子额度/响应/可见性依据；MRT1和静态出口需求不能包装成现代GIN成本。当前可见硬件不满足现代校准条件，不假定其他机器具备。四请求不能复制为千卡生产样本，1024卡长工作量完整执行仍未完成；独立请求采集连接失败，强基线原版执行、匹配校准和规模泛化仍缺失，论文目标保持in_progress。

上一阶段平台`52e1afb`、研究`bfa2e7d`、收尾`bf57681`，证据见[前阶段核对](../review_artifacts/inference_resilience_20261022/completion_audit.md)。旧阶段记录与原始产物保留，不修改为新目标已经完成。
