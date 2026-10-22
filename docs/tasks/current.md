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

当前下一步：核查固定NCCL provider的GIN上下文/连接/事务/flush和资源边界，完善现代操作依赖规范；结合实际设备信息决定可校准目标，再实现持续连接与追加工作的最小后端改法。不得先加QP复用标签、提前加入未来数据或删屏障制造收益。校准后再对比正常无故障真实路由共享网络中的排队和方法排序，恢复只作补充。可用两机RDMA设备信息已询问，未答复前不假定资源。四请求不能复制为千卡真实生产样本，1024卡长工作量完整执行仍未完成；留出数据、强基线、匹配校准与规模泛化仍缺失，论文目标保持in_progress。

上一阶段平台`52e1afb`、研究`bfa2e7d`、收尾`bf57681`，证据见[前阶段核对](../review_artifacts/inference_resilience_20261022/completion_audit.md)。旧阶段记录与原始产物保留，不修改为新目标已经完成。
