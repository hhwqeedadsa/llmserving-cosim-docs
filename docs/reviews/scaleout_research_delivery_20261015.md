# Scale-Out三项研究要求的交付核对

本页核对用户提出的千卡网络方案、MoE训练热点研究与仓库框架文档。证据来自本地代码、实际探针、公开论文及固定源码；不将方案等同于千卡运行或训练实测。用户已明确原始JSON稍后提供，相关真实统计保持待补。

| 用户要求 | 可证明的交付 | 核查结论 |
|---|---|---|
| 千卡能否做到、如何做 | [规模报告](scaleout_scaling_hotspots_20261015.md)§1：固定数组和N²元数据问题、0/8交换机实际分配、16→64→256→1024验收、短窗逐包与校准流级路线；[资源摘要](../../experiments/scaleout_scaling/results.json) | 已给出条件化可行性和实施方法；没有千卡执行证据，报告明确保留这个限制 |
| 输入batch、All-to-All与EP/DP选择 | 报告§2–3：局部/全局batch、训练累积、EP分组、All-to-All-v、两种通信模板、NIC/rail与带宽；[核算CLI](../../examples/scaleout_capacity/README.md)已实际运行 | 已交付定义、选择准则和可修改核算入口；公开CLI与13项相关检查见[验证摘要](../../experiments/scaleout_scaling/validation.json) |
| 研究训练热点及仿真缺陷 | [训练报告](moe_training_hotspots_20261015.md)：DeepSeek-V3原始论文、Megatron固定4文件、SimAI/ASTRA具体等分collective分支；[来源身份](../../experiments/scaleout_scaling/training-sources.json) | 已从训练机制、集合展开和网络映射说明问题；random、等量collective和真实路由分开，不泛称所有平台只用random |
| 查看所给HF数据并明确适用范围 | 既有[固定清单](../../fixtures/m10_route_selection/README.md)、manifest、原始解析代码及选择摘要；本轮新HF元数据访问超时也已记录 | 已核查本仓用过的R1-AWQ/MMLU子集为推理，未外推整个远程数据集；不把推理数据当训练step序列 |
| 给出热点的解决/验证方法 | 训练报告§5–6和[确定性实验](../../experiments/scaleout_scaling/README.md#专家热点的确定性反例)：按专家→rank→节点→NIC/端口分层，保留accepted/pad/split与依赖，设计真实/打乱/均匀/放置对照 | 5个小例实际执行，整数结果与独立手算一致；不把payload或token窗口峰值称为排队/加速比 |
| 仓库结构与third_party/每轮布局文档 | [组织方案](../repository_layout.md)：现有优点/耦合、目标依赖、文件映射、独立后端构建、正式依赖与研究clone、实验/迭代/产物位置、迁移验收；[迭代实例](../iterations/20261015_scaleout_scaling.md) | 已交付具体可执行规范并更新导航；没有创建空模块或复制多套源码来冒充重构 |
| 本地修改、不push | 公共功能与研究分别本地提交；子模块gitlink未改 | 保留原有依赖和UB能力，未发布 |

本轮新分析程序只用Python标准库；运行预算15s、RSS256MiB/地址空间512MiB、产物16MiB、磁盘预留8GiB。真实命令和资源保存在 `runs/scaleout_training_review_20261015/`；[反例结果](../../experiments/scaleout_scaling/routing-counterexample-results.json)保存输入/分析SHA-256、合成标签、手算检查和运行信息。论文各章节锚点、固定4份源码hash、本地链接均已检查。

以下是明确未完成的**实现/实验后续**，不能从研究交付推断其通过：非UB在线服务、RDMA边界修复、千卡共享网络逐包执行、真实训练trace采集/统计、硬件校准及优化收益。它们已经在方案中按先后关系列出；原始路由JSON按用户要求暂缓，不再重复请求或替换成合成数据冒充真实结果。后续动态状态见[当前任务](../tasks/current.md)。
