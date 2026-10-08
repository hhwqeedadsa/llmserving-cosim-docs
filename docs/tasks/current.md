# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。整体状态：`in_progress`。

当前批次：**LOAD 响应拆分与完成过程对照**，状态：`completed`。六组执行与比较通过，整理本地提交后按已有授权转入物理出口服务变化。本阶段按实验批次汇报，不另立平台 M 编号。

依据为用户的[实验方案 v0.1](../../experiments/scaleup_observability/plan_v0.1.md)。原方案未改写；当前工作仍是现象发现、成因验证、观测需求判断。

## 全局进度与证据

| 工作 | 状态与证据入口 |
|---|---|
| 九组正常小场景与统一概览 | 已完成；[首批结果](../../experiments/scaleup_observability/results.md) |
| 发送槽定向对照与机制核验 | 已完成；[发送槽结果](../../experiments/scaleup_observability/results-slots.md) |
| 发送槽现象的基础与新增状态观测比较 | 已完成首轮；[基础比较](../../experiments/scaleup_observability/results-observations.md)、[新增状态比较](../../experiments/scaleup_observability/results-send-state.md)；所需平台记录曾独立验收，见[支撑证据](../../fixtures/m17_send_state/README.md) |
| 专家计算节奏 | 已完成六组；0.5/1/2 倍成本下双槽收益为 25.68%/19.97%/15.02%，排队代价同时保留；[结果](../../experiments/scaleup_observability/results-tempo.md) |
| LOAD 消息拆分与完成 | 已完成六组；少分段减少网络字节却增加完成时延，见[拆分结果](../../experiments/scaleup_observability/results-load-grain.md) |
| 外部出口服务变化 | 待执行，检查局部约束是否命中业务、如何传播及影响执行 |
| 汇总筛选与针对性深化 | 待上述批次完成后保留一至两项现象，补必要成因/观测对照；采样优化为候选 |
| 完整场景代表性验证与总结 | 最后选择少量场景，先核对能力、来源及剩余资源 |

当前已解释发送槽限制，尚没有持续信用反压传播或异常内存事务拖尾的充分证据。一条现象分支完成观测比较不等于整体实验完成。

## 本批目标与状态

按[LOAD 拆分方法](../../experiments/scaleup_observability/load-grain-method.md)执行：固定 seed=4、64 请求和原始计算成本，LOAD 响应载荷 128/512/2048 B 与单槽/双槽，共六组。核对实际响应份数、数据就绪、协议完成、发送资源释放和整轮影响；其他业务与计算保持一致。

38 项实验检查、旧比较回归和六组执行通过，方法与实现摘要保持一致。结果及资源索引见[拆分结果](../../experiments/scaleup_observability/results-load-grain.md)，用户操作见[条件探索入口](../../experiments/scaleup_observability/exploration-usage.md)。下一批固定双槽、三类拓扑出口与 80%/50% 速率，另加新正常基准；不按结果挑选热点。

## 产物、预算与衔接授权

- 当前输入、执行、分析：`runs/scaleup_observability/load-grain-v1/`；检查与实际命令：`runs/scaleup_observability/load-grain-checks/`。
- 上一批：`runs/scaleup_observability/tempo-v1/` 和 `tempo-checks/`；其他历史产物通过各结果页索引，不移动或改写。
- 小场景沿用 RSS 8 GiB、地址空间 12 GiB、每阶段 1800 秒、累计 48 GiB、至少 20 GiB 空闲；旧累计目录继续计入。
- 最近完成批次的累计快照约 38.202 GiB；下批七次拟预留 5.25 GiB，运行前重新预检。失败保留原因，不自动重跑、提高限额或清理旧产物。
- 2026-10-08 用户允许在上述主线内按批次自主衔接；各批验证通过后整理记录、本地提交，不推送。改变研究定义、增加未授权平台模型或提高资源预算时另行明确。
