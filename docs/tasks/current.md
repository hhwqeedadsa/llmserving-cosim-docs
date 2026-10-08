# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。整体状态：`in_progress`。

当前批次：**物理出口服务变化对照**，状态：`completed`。七组执行与比较通过，整理本地提交后继续完整首轮代表性验证；不另立平台 M 编号。

依据为用户的[实验方案 v0.1](../../experiments/scaleup_observability/plan_v0.1.md)。原方案未改写；当前工作仍是现象发现、成因验证、观测需求判断。

## 全局进度与证据

| 工作 | 状态与证据入口 |
|---|---|
| 九组正常小场景与统一概览 | 已完成；[首批结果](../../experiments/scaleup_observability/results.md) |
| 发送槽定向对照与机制核验 | 已完成；[发送槽结果](../../experiments/scaleup_observability/results-slots.md) |
| 发送槽现象的基础与新增状态观测比较 | 已完成首轮；[基础比较](../../experiments/scaleup_observability/results-observations.md)、[新增状态比较](../../experiments/scaleup_observability/results-send-state.md)；所需平台记录曾独立验收，见[支撑证据](../../fixtures/m17_send_state/README.md) |
| 专家计算节奏 | 已完成六组；0.5/1/2 倍成本下双槽收益为 25.68%/19.97%/15.02%，排队代价同时保留；[结果](../../experiments/scaleup_observability/results-tempo.md) |
| LOAD 消息拆分与完成 | 已完成六组；少分段减少网络字节却增加完成时延，见[拆分结果](../../experiments/scaleup_observability/results-load-grain.md) |
| 外部出口服务变化 | 已完成七组，逐包确认生效，局部队列增加但整轮变化小且非单调，见[结果](../../experiments/scaleup_observability/results-service.md) |
| 汇总筛选与针对性深化 | 保留发送槽等待与响应粒度取舍；发送槽已有独立观测比较，不新增分类算法，待完整代表性结果后统一收尾 |
| 完整场景代表性验证与总结 | 最后选择少量场景，先核对能力、来源及剩余资源 |

当前已解释发送槽限制，尚没有持续信用反压传播或异常内存事务拖尾的充分证据。一条现象分支完成观测比较不等于整体实验完成。

## 本批目标与状态

按[出口服务方法](../../experiments/scaleup_observability/service-method.md)执行：seed=4、64 请求、原始计算和双槽，三个拓扑出口各 80%/50% 速率，另加新正常基准。区间固定为 [14,007, 42,022) ns；不根据结果选择热点或改变时间窗。

42 项实验检查、原正常概览回归、13 项递归依赖检查、七组执行及统一比较通过；详见[结果](../../experiments/scaleup_observability/results-service.md)。下一批按[代表性方法](../../experiments/scaleup_observability/representative-method.md)选择 L1 上行 50%，保留原完整切片参数与共享图；只扩展实验准备和流式读取。

## 产物、预算与衔接授权

- 当前输入、执行、分析：`runs/scaleup_observability/service-v1/`；检查与实际命令：`runs/scaleup_observability/service-checks/`。
- 上一批：`runs/scaleup_observability/load-grain-v1/` 和 `load-grain-checks/`；其他历史产物通过各结果页索引，不移动或改写。
- 小场景沿用 RSS 8 GiB、地址空间 12 GiB、每阶段 1800 秒、累计 48 GiB、至少 20 GiB 空闲；旧累计目录继续计入。
- 最近完成批次的累计快照约 38.325 GiB；完整场景每次预留 6 GiB并逐次预检，后端沿用 RSS 48 GiB、地址空间 56 GiB、7200 秒，其余阶段仍用小场景预算。失败保留原因，不自动重跑、提高限额或清理旧产物。
- 2026-10-08 用户允许在上述主线内按批次自主衔接；各批验证通过后整理记录、本地提交，不推送。改变研究定义、增加未授权平台模型或提高资源预算时另行明确。
