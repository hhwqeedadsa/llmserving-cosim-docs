# 当前任务与交接

**E1、E2 交付收尾已完成，状态 `completed`。** 按用户 2026-10-10 的确认补齐时长和实际路径证据，修正阶段结束判断，整理记录并本地提交。用户随后确认将已完成提交及相关子模块发布到 GitHub，目标为主仓库 `main` 与 ASTRA-sim、LLMServingSim 的 `cosim` 分支。平台 P 部分（M18～M21）保持已完成；本次没有启动 E3、E4 或新的平台任务。

## 范围与结果

| 项目 | 状态 | 结果与证据入口 |
|---|---|---|
| 平台 P：M18～M21 | `completed` | 固定 `e3db37a67ebb13edd5dd522dd1f8028cc46c2e19`，平台及子模块未改变；[平台验收](../../fixtures/m21_platform/README.md) |
| E1 正常业务探索 | `completed` | 四次正常运行、初态比较及全 2688 出口非空时长界限；明确原聚合精度；[E1 结果](../../experiments/scaleup_issue_screening/results/e1/README.md) |
| E2 成因与稳健性 | `completed` | 三项原对照及四份后端补采；实际路径、不扰动和选定出口精确时长核验通过；[E2 结果](../../experiments/scaleup_issue_screening/results/e2/README.md) |
| E3 测量价值 | `pending` | 未执行；可以考虑具体端点定位任务，不能因所有网络等待未分清而一概排除 |
| E4 完整场景 | `conditional` | 未执行，仍以成因和信息比较通过为前提 |

只保留一个候选 C1：供数准备拖后消费者并传递到后层。专家准备成本作用有证据；首次交换机发送前的剩余过程尚未完全归因。没有证明端网映射有收益或无收益，也没有硬件校准或泛化结论。[研究结论](../../experiments/scaleup_issue_screening/conclusions.md)解释发现与限制，[原方案](../../experiments/scaleup_issue_screening/plan_v0.1.md)规定研究门槛。

## 验证与保存

[交付复核](../../experiments/scaleup_issue_screening/results/delivery-review.md)按原方案逐项核对；[统一验收](../../experiments/scaleup_issue_screening/results/delivery/acceptance.json)通过。5 项新增检查、11 项原实验工具检查、独立合成完整流程及 13 项依赖检查通过。平台校验、实验解释和硬件校准分别陈述，没有把只读检查当成构建。

原七个归档保留，新增四个补采包约 0.858 GiB，已逐文件核验后释放本次展开文件。累计产物采样峰值 47.718 GiB，低于原 48 GiB 上限。后端回放不执行前端或重新构图；[资源汇总](../../experiments/scaleup_issue_screening/results/resources.md)保存实际耗时及计量边界。

详细时长表、逐包证据、命令和日志在 `runs/scaleup_issue_screening/delivery/`；[补证索引](../../experiments/scaleup_issue_screening/results/delivery/README.md)说明目录，[实验操作](../../experiments/scaleup_issue_screening/usage.md)提供公开可复用命令。方法、结果与运行日志分别保存，当前页只负责交接。历史阶段见[里程碑](../milestone_history.md)。

本次提交恢复既有类型前缀，规范已写入[开发维护](../development.md)；没有改写被证据引用的历史提交。发布按 ASTRA-sim、LLMServingSim、主仓库的顺序进行，命令、退出状态及远端提交核验保存在 `runs/git-publish/20261010-e1-e2/`。当前停止在 E1/E2 收尾完成处，下一阶段由用户确认。
