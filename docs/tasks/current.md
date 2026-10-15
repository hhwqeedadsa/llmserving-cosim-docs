# 当前任务与交接

状态：`in_progress`。2026-10-15，按用户要求审阅本地 `/home/lry/moe-ub-cosim`，与既有模型/Batch/Block 网络实验比较，调研并补充 Scale-Out。全部修改留在此仓库，验证后本地提交，**不 push**。

## 本轮新增范围

2026-10-15 后续目标：小规模Scale-Out基础仿真，形成LLMServingSim到非UB ns-3的完整流程，并可视化。用户最新要求先交PDF审阅；本阶段只整理方法，不启动实验，不将静态拓扑或离线回放当作在线闭环。方法原文在 [scaleout_e2e/plan.md](../../experiments/scaleout_e2e/plan.md)，[PDF审阅稿](../review_artifacts/ScaleOut实验方法_审阅稿_v0.1_20261015.pdf)采用8端点、5种拓扑、10个拟运行条件及G0—G3门槛。PDF已导出并核对8页A4、中文字体嵌入与可搜索文本；状态为待用户审阅。生成/预览不超过60s、RSS1GiB/地址空间2GiB、产物32MiB；日志 `runs/scaleout_method_pdf_20261015_final/`。没有新增网络执行或后端修改。

2026-10-15 新需求：按用户两张图实现可参数化的非UB Scale-Out物理拓扑，覆盖两层leaf-spine、ToR—Agg—电交换Core、多rail与HPN参考双平面。每层数量、分组、fanout、NIC映射、端口与分层带宽可配置；生成固定后端可读取的拓扑、显式映射与审计，先小例与千卡静态生成验证。区别rail/plane/pod/segment，OCS改普通交换机；不宣称复现完整HPN协议或已接入非UB在线服务。生成/检查限300s、RSS1GiB/地址空间2GiB、产物128MiB；小型原生装载探针按既有构建预算，最多16交换机、不运行千卡逐包。只在本地提交、不push。

本次拓扑生成与静态验收已完成：公共 `scaleout topology`、完整参数说明、四套1024 GPU配置和八卡小图已交付；23项相关检查及5套配置各两遍的逐字节复现通过。三层Clos为128/128/64个ToR/Agg/Core，HPN参考双平面为64/32/8；独立8rail示例如实报告458752个无序GPU对不可达。示例见[入口](../../examples/scaleout_topology/README.md)，结构与来源见[说明](../reviews/scaleout_topology_20261015.md)。研究侧完整日志与小摘要独立保存于 `experiments/scaleout_topology/`，本地提交分别保存平台与验收。

原生装载保持pending：本轮磁盘检查低于8GiB预留，未构建/运行装载探针，未扩限或删除未知数据。原生格式只经过源码匹配与独立解析；不表示已执行千卡网络、分流/故障恢复、RDMA回调修复或非UB在线服务。该限制不影响本次物理生成器的静态结果。

拓扑平台本地提交 `a879128`；独立验收及来源另作研究提交。完整命令与11个受限子任务退出状态在 `runs/scaleout_topology_validation_20261015_final/`，版本化[结果摘要](../../experiments/scaleout_topology/results.json)与[迭代记录](../iterations/20261015_scaleout_topology.md)已保存；原生依赖gitlink保持不变。

2026-10-15：继续研究非 UB Scale-Out 的千卡资源可行性、batch/All-to-All/EP/DP 设计、真实 MoE 热点与所给 Hugging Face 路由数据；结合当前仓库交付架构及迭代布局规范。先做有界来源与资源探针，不启动千卡全栈重跑，不下载大型数据。全部在师兄仓库本地保存、不 push。

用户已要求原始数据稍后再处理。本轮不依赖原始 JSON 的代码、研究和文档已完成；真实热点统计保持 pending，不反复请求路径。详细范围及证据见[本轮记录](../iterations/20261015_scaleout_scaling.md)。

补充研究已完成：核查 DeepSeek-V3 v1 论文与 Megatron `915b1d2d...` 的4份路由/通信源码，补足训练平衡、节点限制、容量与padding依据；定位锁定SimAI/ASTRA的等分All-to-All路径；5个合成反例的跨机字节及窗口计数与手算一致。详见[训练热点报告](../reviews/moe_training_hotspots_20261015.md)和[结果](../../experiments/scaleout_scaling/routing-counterexample-results.json)。仅阅读小型公开材料，不安装训练框架、不获取原始路由；来源/日志 `runs/scaleout_training_review_20261015/`。实际核算限15s、RSS256MiB/地址空间512MiB、累计16MiB、磁盘预留8GiB；链接、来源hash和合成标签已检查，不触发平台/完整网络回归。

本次“三项研究与解决方法”交付核对见[证据表](../reviews/scaleout_research_delivery_20261015.md)。研究方案和文档已交付；本页整体仍记录此前授权的更大范围后端任务，pending项不因研究交付而改为完成。

## 范围与次序

1. 先理解调度、构图、ASTRA、唯一 ns-3 时钟、事务完成、观测与真值隔离，再确定修改。
2. 按最新指令，Scale-Out 改为研究**非 UB 的 ns-3 / ns-3-alibabacloud**：核查 ASTRA 原生接口、NIC/rail/Clos 架构、RDMA 完成语义与接入范围。原 UB 分层功能保留为独立基线，停止将其作为 Scale-Out 实现路线。
3. 平台功能与研究性能结论分开。不把 UB 链路称为 RoCE，不以静态生成检查代替实际仿真；不同 ns-3 版本不得混链，也不将两个独立仿真相加冒充联合执行。

## 状态与证据

| 项目 | 状态 | 说明 |
|---|---|---|
| 整体架构与旧代码比较 | completed | 从 `dd7d8e8` 开始；[审阅报告](../reviews/scaleout_review_20261015.md) |
| UB 分层输入基线 | completed | 26 项相关单元检查、两个实际联合条件通过；服务器 100G/10G 时结束于 117098/146942ns，仅为 synthetic UB 功能验证。`runs/scaleout_validation_20261015/integration-02/summary.json` |
| 固定版本 UB 构建 | completed | 13 项依赖通过；新构建 `build/scaleout` 成功。工具链、峰值资源和失败尝试均保存在 `runs/scaleout_validation_20261015/`，gitlink 未改 |
| 非 UB Scale-Out 源码研究 | completed | 锁定 ASTRA 原生 ns-3 3.42、Alibaba 3.36.1 派生库；核查完成回调、CC 模式、NIC/rail 放置；[源码报告](../reviews/scaleout_ns3_backends_20261015.md) |
| 非 UB 实际探针与统一复现 | completed | 真实编译阿里 core 与 RDMA 所需模块；时间构造 1000 倍差异、8 条直连消息的发送回调缺失/重复已复现。统一脚本和版本化记录在 `experiments/scaleout_backend_review/`；完整日志 `runs/scaleout_ns3_review_20261015/` |
| 非 UB 在线服务后端 | pending | 研究完成后按独立可执行程序接入；当前不能用 `moe-ub run` 执行 RoCE，不声称已实现 |
| 千卡容量/工作量公共入口 | completed | `scaleout capacity` 已交付；TP=PP=1，检查 EP、batch、两层 Clos 和固定数组。示例为1024GPU/EP64/16副本；[用法](../../examples/scaleout_capacity/README.md)。估算不是网络执行或显存校验 |
| 交换机内存实际探针 | completed | 阿里锁定库真实构建，0/8个对象及统一脚本复现；8个节点RSS增270,532,608 B。192节点约6.01GiB为固定数组推算，未实际执行千卡；[证据](../../experiments/scaleout_scaling/results.json) |
| 训练/推理热点研究与网络方案 | completed | [研究报告](../reviews/scaleout_scaling_hotspots_20261015.md)明确四程激活/梯度DP区别、All-to-All-v、NIC/rail和16→1024验收路线。所用HF子集为推理，不能作为训练结论 |
| 原始真实路由统计 | pending | 本机未找到JSON，用户表示稍后处理；清单和来源校验已有，不产生虚构热度结果。训练数据仍需有训练step与来源依据 |
| 代码框架与迭代规范 | completed | [布局方案](../repository_layout.md)区分现有与目标结构，明确third_party/研究clone、平台/实验、每轮记录；未批量迁移源码 |
| 本轮验证 | completed | 13项相关单元检查、公开容量CLI、真实探针统一复现、10篇文档本地链接及13项依赖检查通过；[摘要](../../experiments/scaleout_scaling/validation.json)。没有新执行网络或硬件校准 |

预算：输入生成/单元验证时限 300 秒、地址空间 2 GiB、产物 256 MiB。独立构建限时 600 秒、RSS 4 GiB、地址空间 8 GiB、构建与本任务日志累计 1.5 GiB、磁盘预留 8 GiB、编译并发 2。后端按示例显式预算执行，不自动提高限额。完整 CM384/R1 不属于此次验收。构建/工具链记录：`runs/scaleout_validation_20261015/`。

非 UB 探针独立预算：配置 120s、构建 300s、每例执行 15s；RSS 2GiB、地址空间 4GiB，研究来源与构建累计 512MiB、磁盘保留 8GiB；短运行用更低内存上限。真实构建/运行完成不表示发送语义检查通过：结果明确保留 `all_send_callbacks_once=false`。尚未运行 Clos/rail 交换机拥塞对照，未接入非 UB 在线服务。

本轮内存探针沿用上述构建预算，分配例RSS512MiB/地址空间1GiB，累计来源/构建/本次输出512MiB；资源日志 `runs/scaleout_scaling_20261015/`。相关单元检查限300s、RSS1GiB/地址空间2GiB、产物256MiB；实际13项通过。不扩限、不下载大数据、不运行完整CM384/R1。公共核算使用有界参数和最多64KiB输入，不生成逐包事件。

UB 平台基线已本地保存为 `5a3761f`。源码研究及探针另行本地提交；不 push。机器未配置 Git 作者，使用命令作用域 `Codex <codex@local.invalid>` 保存本任务提交，没有修改用户或全局 Git 配置。后续实现按非 UB 报告第 8 节从 RDMA 完成语义与网络微基准开始。

非UB接口研究提交为 `b26e211`，公共容量入口提交为 `50c94da`，规模探针/报告/布局规范为 `09be7c6`；训练来源与反例另作研究提交。所有gitlink保持原样，全部修改只在本地。

上轮 Scale-Up 筛选已完成，旧记录见 Git `dd7d8e8:docs/tasks/current.md` 与 [研究结论](../../experiments/scaleup_issue_screening/conclusions.md)，不重启其后续算法工作。
