# 当前任务与交接

**当前任务：E1、E2 交付收尾，状态 `in_progress`。** 用户于 2026-10-10 授权补齐排队持续时长与实际路径核验，整理结果并本地提交。平台 v0.5 的 P 部分（M18～M21）已完成；七次原执行及主要分析保留，原验收缺口见[交付复核](../../experiments/scaleup_issue_screening/results/delivery-review.md)。本次不启动 E3、E4，不修改固定平台或提高资源上限。

## 依据与文档分工

| 文件 | 职责 |
|---|---|
| [v0.5 设计档案](../archive/moe_ub_cosim_dev_v0.5.md) | 已验收的平台增量要求 |
| [用户指南](../user_guide.md)、[A3 示例](../../examples/a3_segment/README.md) | 准备、运行、记录、保存、采样和回放操作 |
| [M21 验收](../../fixtures/m21_platform/README.md) | 平台输入、测试、资源与证据 |
| [实验方案](../../experiments/scaleup_issue_screening/plan_v0.1.md) | 正常探索、候选、成因和测量的进入与结束条件 |
| [研究结论](../../experiments/scaleup_issue_screening/conclusions.md) | 本轮发现、限制和停止原因 |
| [交付复核](../../experiments/scaleup_issue_screening/results/delivery-review.md) | 原方案逐项符合情况、缺口与原验收判断更正 |
| [实验操作](../../experiments/scaleup_issue_screening/usage.md) | 用户可复用的命令及输入输出位置 |
| 本页 | 范围、状态、证据入口和停止位置 |

维护和 Git 约束见 [AGENTS.md](../../AGENTS.md)与[开发维护](../development.md)。平台与实验分别验收；历史阶段见[里程碑](../milestone_history.md)。

## 阶段结果

| 阶段 | 状态 | 结果与证据 |
|---|---|---|
| M18（P1）来源与执行条件 | `completed` | 固定 CANN 来源和参考映射；[来源核对](../../fixtures/m18_a3_execution/README.md) |
| M19（P2）资源与关键依赖 | `completed` | 独立核、缓冲、真实完成与 Token 归约；[小型验收](../../fixtures/m19_a3_resources/README.md) |
| M20（P3）内部事实与有限层段 | `completed` | 公共准备、运行、事实导出、保存与回放；[流程证据](../../fixtures/m20_segment_facts/README.md) |
| M21（P4）平台独立验收 | `completed` | 四类小测试、采集不扰动、128/1024 请求三层执行与切片核验；[最终验收](../../fixtures/m21_platform/README.md) |
| E1 正常业务探索 | 验收 `pending` | 四次运行与主要分析完成；待补排队持续时长的定义和交付；[正常结果](../../experiments/scaleup_issue_screening/results/e1/README.md) |
| E2 成因与稳健性 | 验收 `pending` | 三次对照及依赖核验完成；待补实际路径核验，并修正阶段结束判断；[成因结果](../../experiments/scaleup_issue_screening/results/e2/README.md) |
| E3 测量价值 | `pending` | 未执行；需按具体诊断任务判断，不能以所有网络等待未分清一概排除 |
| E4 完整场景 | `conditional` | 未执行；仍以成因及信息比较通过为前提 |

## 结论与范围

平台实验基线固定为 `e3db37a67ebb13edd5dd522dd1f8028cc46c2e19`。E 部分没有改变平台、子模块或锁文件；固定平台差异检查和 13 项依赖检查通过。P 的子模块改动已由内向外保存，源码依据与校准边界见[模型依据](../model_validation.md)。

正常业务中热点供数端会拖后其他消费者，并将完成差异传递到后层进入。256 请求下，专家成本减半、加倍使最后一层实际计算结束分别变化 −25.06%、+50.38%；交换机队列总量并不与业务耗时同向。替代复制完成条件只改善 1.22%，主要形态保留，具体最晚消费者与关键事件发生变化。

准备节奏作用明确、剩余等待无法区分，仍是当前证据支持的结论。方案允许保留未知，但这不免除明确要求的记录和核验；也不自动排除端点定位等较窄 E3 任务。未验证映射收益或无收益，也未证明现有观测足够。仅一个真实来源组，计算与资源仍有参考假设；短段不代表稳态，对照只覆盖 256 请求档。

## 保存与检查

七次正式运行全部通过公开校验并无损归档。短段保留可恢复切片；六层段和三项对照保留完整原始运行，省略额外切片。展开文件与本次临时恢复的基线副本在逐文件核验后释放，旧任务产物保留。资源上限未提高，实际累计采样峰值约 47.45 GiB；[资源汇总](../../experiments/scaleup_issue_screening/results/resources.md)记录阶段耗时、内存、空间和采样限制。

11 项实验工具检查及三种独立合成完整流程通过，合成结果不作为研究样本。完整日志、命令、工作目录和退出状态在 `runs/scaleup_issue_screening/`；复核摘要、配置和工具在 `experiments/scaleup_issue_screening/`。查看结果先读研究结论；逐 rank/计算/端口统计在 `analysis/`，两组内部核验在 `facts/`，原始运行及清单在 `archives/`。需要再次核验完整输入时按操作说明恢复到新目录。

本次先核算原有聚合记录可证明的排队非空时长范围，再用独立小流程检验详细采集不扰动执行，串行回放选定传输所需的记录。沿用累计 48 GiB 等既定预算，补证完成后逐项复核 E1/E2，明确候选和 E3 判断范围并停止。提交采用既有类型前缀；不改写已被证据引用的历史提交，不推送。
