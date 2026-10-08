# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。整体状态：`completed`。

2026-10-08 已完成原方案中选定的探索、成因核验、观测比较及最后两组完整首轮验证。结果先读[研究总结](../../experiments/scaleup_observability/conclusions.md)。依据仍为用户的[实验方案 v0.1](../../experiments/scaleup_observability/plan_v0.1.md)，原文未改写，没有新增平台里程碑。

## 目标与完成范围

以现象发现、成因验证和观测需求判断为主线。正常小场景统一观察全部 32 个交换机出口，依据结果选择配置和服务对照；测量程序与内部核验资料保持分离。完整场景最后执行，复用固定真实路由首轮与已有图缓存。

| 工作 | 结果与证据入口 |
|---|---|
| 九组正常小场景与统一概览 | 已完成；[首批结果](../../experiments/scaleup_observability/results.md) |
| 发送槽定向对照与机制核验 | 已完成；[发送槽结果](../../experiments/scaleup_observability/results-slots.md) |
| 基础与新增状态的观测比较 | 已完成选定规则和新输入复核；[基础比较](../../experiments/scaleup_observability/results-observations.md)、[端侧状态比较](../../experiments/scaleup_observability/results-send-state.md)；支撑记录曾独立验收，见[平台证据](../../fixtures/m17_send_state/README.md) |
| 专家计算节奏 | 六组完成；三档成本下双槽收益与排队代价均保留，见[结果](../../experiments/scaleup_observability/results-tempo.md) |
| LOAD 响应拆分与推进 | 六组完成，并只读核验全部配对跨服务器任务的真实路径与数据到齐，见[拆分结果](../../experiments/scaleup_observability/results-load-grain.md)、[路径证据](../../experiments/scaleup_observability/results-response-paths.md) |
| 外部出口服务变化 | 七组完成，逐包确认生效，局部排队与整轮变化并非简单对应，见[结果](../../experiments/scaleup_observability/results-service.md) |
| 完整首轮代表性验证 | 正常与 L1 上行半速两组完成；工作量相同，正常组精确复现 48,446,098 ns，半速组增加 74,238 ns，见[完整结果](../../experiments/scaleup_observability/results-representative.md) |
| 阶段现象、解释及后续取舍 | 已完成；[研究总结](../../experiments/scaleup_observability/conclusions.md) |

本阶段最明确的测量需求是保留端侧实际待发和资源占用信息，同时解决周期采样的短等待漏检。响应拆分已核对实际路径与完成时序，但未独立分离多路径、流水和请求资源贡献。现有证据不足以确认持续信用反压传播；完整场景没有验证小场景的采样覆盖率。

## 验证与资源

46 项实验程序检查及 3 项新增响应路径检查通过；路径分析公开入口复现同一摘要。完整两组的重放、导出、公开校验、流式统计与配对比较均通过，固定方法和既有实现摘要不变。13 项递归依赖一致，平台源码和锁定子模块未修改。

完整阶段九个受监控步骤累计约 2.82 小时；采样 RSS 峰值约 45.53 GiB。累计登记产物最终快照约 41.10 GiB，含临时文件的采样峰值约 41.24 GiB。完整后端 RSS 48 GiB、地址空间 56 GiB、7200 秒，其他阶段 RSS 8 GiB、地址空间 12 GiB、1800 秒，以及累计 48 GiB、至少 20 GiB 空闲的预算均保持原值。未下载、重新展开大图、清理旧产物或重跑完整仿真。

详细结果、命令与资源报告入口：

- 完整两组：`runs/scaleup_observability/representative-v1/`，检查与收尾：`representative-checks/`。
- 只读路径证据：`runs/scaleup_observability/response-paths-v1/`，公开复核：`response-paths-public-check/`，检查：`response-paths-checks/`。
- 小场景各批输入、运行与检查位置由对应结果页索引；公开操作统一从[实验首页](../../experiments/scaleup_observability/README.md)进入。

## 交接

开发期间各批验证后仅本地提交。2026-10-08 用户另行确认发布，实验交付 `7a9daef` 已推送至主仓库 `origin/main`；依赖按由内向外顺序发布，ASTRA-sim `cosim` 为 `93fe5a4`，LLMServingSim `cosim` 为 `1e8bb54`，ns-3-UB `cosim` 已同步且仍为 `c0f1fe5`。更深层依赖保持原固定提交。实际命令、退出状态与远程核对记录在 `runs/publish-20261008T235201Z/`；本地大产物不随 Git 发布。

本阶段到此收尾。[后续事项](../future_iterations.md)保存短等待采集及响应机制等候选，它们不自动成为新的执行任务。
