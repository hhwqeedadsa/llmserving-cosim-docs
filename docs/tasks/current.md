# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。整体状态：`in_progress`。

当前批次：**专家计算与通信节奏对照**，状态：`completed`。2026-10-08 六次小场景及统一比较通过；已获准继续消息拆分与后续主线批次。

依据仍是用户的[实验方案 v0.1](../../experiments/scaleup_observability/plan_v0.1.md)，按现象发现、成因验证、观测需求判断推进。用户已指出任务主线被误写为平台开发；“M17”仅为助手给先前支撑改动添加的历史标识，不是用户另立的研究阶段。原方案未改写，本轮没有平台或子模块改动。

## 已完成工作与证据

| 工作 | 结果与入口 |
|---|---|
| 九组正常小场景及统一概览 | [首批结果](../../experiments/scaleup_observability/results.md) |
| 单槽/双槽定向对照 | [发送槽结果](../../experiments/scaleup_observability/results-slots.md) |
| 端口计数与计数加队列的基础观测对照 | [基础观测结果](../../experiments/scaleup_observability/results-observations.md) |
| 按上述缺口补充实际端侧发送资源记录 | 独立平台支撑已验收，主仓 `e66a6ae`；[功能证据](../../fixtures/m17_send_state/README.md)、[用户示例](../../examples/send_state/README.md) |
| 使用新增状态的研究对照 | 四次全新小场景及五种采样网格完成；[研究结果](../../experiments/scaleup_observability/results-send-state.md)、[数值与摘要](../../experiments/scaleup_observability/results-send-state-v1.json) |

本轮 seed=3 为已见输入的复现参照，seed=4 为新输入复核，各单槽/双槽。三档观测来自相同执行；方法只读隔离公开包，独立标签留在评价侧。方法、用户操作与结果分别见[方法](../../experiments/scaleup_observability/send-state-method.md)、[操作](../../experiments/scaleup_observability/send-state-usage.md)和上述结果页。

新输入在 100 ns 采样下，新增状态将检查范围从队列基线的 43.05% 减到 10.23%，等待覆盖率从 99.98% 降到 90.28%；双槽单独只有 76.62% 覆盖。采样点判断一致不代表连续等待已完整捕获，不能把未选中的窗口直接排除。更粗采样的遗漏与起点敏感性见结果页。

32 项实验检查、四次完整小型用户流程、七个观测准备/测量/评价阶段和 13 项递归依赖核对通过。执行前固定的实现、方法和原方案摘要未改变。没有重跑仿真、改变研究规则或提高预算。

## 当前范围与后续顺序

本批按[计算节奏方法](../../experiments/scaleup_observability/tempo-method.md)执行：固定 seed=4、64 请求，专家主体计算成本 0.5/1/2 倍与单槽/双槽，共六组；实际图和回调核对工作量及等待，方法在运行前固定。用户入口、方法与结果分开维护。

| 批次 | 状态与目的 |
|---|---|
| 专家计算节奏 | 已完成，双槽整轮收益依次为 25.68%、19.97%、15.02%；[结果](../../experiments/scaleup_observability/results-tempo.md) |
| 消息拆分与完成 | 待执行，核验实际多请求/响应与任务尾段 |
| 外部出口服务变化 | 待执行，检查局部约束的影响与传播 |
| 筛选与针对性深化 | 根据上述证据保留一至两项现象，补必要成因/观测对照；采样优化为候选 |
| 完整场景代表性验证与总结 | 最后选择少量场景，先核对能力、来源及剩余资源 |

用户允许在上述主线内按批次自主衔接，不为普通内部步骤重复确认；各批完成并验证后本地提交，不推送。改变研究定义、增加未授权平台模型或提高资源预算时另行明确。

## 产物与预算

- 当前批次输入、执行、分析：`runs/scaleup_observability/tempo-v1/`；检查与实际命令：`runs/scaleup_observability/tempo-checks/`。
- 上一批证据：`runs/scaleup_observability/send-state-v1/` 和 `send-state-checks/`，主仓提交 `bd01201`。
- 小场景沿用 RSS 8 GiB、地址空间 12 GiB、每阶段 1800 秒、累计 48 GiB、至少 20 GiB 空闲；旧累计目录继续计入。
- 最近累计快照约 38.092 GiB，本批新增约 105.95 MiB；36 项实验检查、六次完整运行和独立比较通过。失败保留原因，不自动重跑、提高限额或清理旧产物。
