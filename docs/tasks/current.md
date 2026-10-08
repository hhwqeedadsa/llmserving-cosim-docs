# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。整体状态：`in_progress`。

本轮执行步骤：**新增端侧状态的观测对照**，状态：`completed`。2026-10-08 已完成四次小场景、三档信息对照、独立评价与结果整理；不表示整体实验结束。

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

## 产物、预算与交付边界

- 输入、执行、隔离观测和评价：`runs/scaleup_observability/send-state-v1/`。
- 实际命令、退出状态、检查日志：`runs/scaleup_observability/send-state-checks/`。
- 沿用阶段 RSS 8 GiB、地址空间 12 GiB、1800 秒、累计 48 GiB、至少 20 GiB 空闲；所有旧累计目录继续计入。
- 最后资源快照约 37.988 GiB，本轮增加约 81.85 MiB；27 个受控阶段约 882 秒，详细口径及索引在结果页。
- 按持续授权完成后本地提交，不推送；没有开始完整场景、新平台模型或另一批实验。

## 下一步候选

优先比较有限记录量下如何保留短等待，例如状态变化记录或窗口内等待时间摘要；先明确可见信息、预算与规则，再开展下一批。计算/通信节奏变化、真实路由及完整场景代表性验证仍未完成。本轮到上述信息比较与记录收尾，不自动启动后续候选工作。
