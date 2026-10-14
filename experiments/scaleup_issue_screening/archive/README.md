# 历史实验与迁移索引

本页区分已经结束的研究分支，并保留原始方案、方法、结果和运行入口。当前要求仅见[现行方案](../plan.md)。历史判断有各自模型与观察范围，不代表整个 Scale-Up 问题域已经被充分探索。

此次入口整理以主仓 `b345694fbc2faed8feebb425d43c60f765f7eee9` 为历史封存点。完整旧文档直接链接该提交，既保留原文和相对链接，也避免在新目录复制整套历史代码。下面标为迁移目标的路径尚不表示已经完成物理迁移。

## 四个历史分区

| 历史分区 | 回答过什么 | 固定证据入口及当前取舍 |
|---|---|---|
| 初始正常探索 | A3 修正后，不同活跃请求数和前置层中有哪些完成差异 | [原方案][initial-plan]、[原方法][old-method]、[初始结果][e1]。保留基线与工作量事实，不将最大值等同于根因 |
| 发送队列 C1 | 计算结束后约 12 μs 的链如何形成，额度和队列组织有什么作用 | [完整成因记录][e2]及其中 C1 章节。现象依赖参考队列和同步映射；固定额度轮转只有有限总体效果，该分支冻结 |
| 网络案例 N1/N2 | 交换机中实际等待是否存在，局部服务变化怎样影响完成 | [网络与服务核验][e2]、[发现档证据][network-result]。保留已核验案例，不代表其他资源与机制已被排除 |
| 操作结束边界 | 操作起止与公开计数能否排除已离开特定出口的旧阶段 | [离线方案][boundary-plan]、[离线结果][e2]及[完整旧结论][old-conclusions]。理想边界有有限增量，尚非必须建设完整映射系统的依据 |

C1、N1、N2 是历史候选标识，新研究的目录和对外报告使用问题名称，不继续按这些内部代号扩展所有内容。

## 原始复现与历史任务

固定版本的[使用说明][old-usage]包含所有历史命令、归档读取和分析入口；[任务快照][old-task]保存上一轮完成与资源记录。旧运行依赖当时代码、子模块与输入，不因后来的目录变更重新绑定版本。

旧 results/e1、results/e2 及机器摘要当前仍在原位置，内容没有改变。其根目录报告、初始计划和多次 candidates 修改的更早版本也保留在 Git 历史。当前 plan_v0.1.md 与 candidates.md 只是历史导航，迁移检查完成后可以删除，不能继续作为并行执行方案。

## 首批迁移对应表

Codex 在本表细化实际路径对应，不另建迁移日记。原始记录与校验摘要保持，移动后的相对引用需修正；若旧文件内容被运行摘要绑定，保留原摘要和旧提交，另记新位置，不回写历史来源。

| 当前内容 | 整理后的归属 | 本次交接处理 |
|---|---|---|
| 原 plan_v0.1.md、历次 candidates.md、旧方法/命令/结论 | 本页固定版本入口；确有离线阅读需要时按问题归档 | 已建立固定链接，原计划位置保留短导航，未批量复制历史 |
| 根目录仍在使用的准备、运行辅助、诊断与评价程序 | analysis/，按职责命名 | 尚未移动；先检查 import、CLI、ROOT 推导和归档路径 |
| 根目录 check_*.py 和工具自检摘要 | tests/ 及其必要证据位置 | 尚未移动；不能改变检查含义或改写旧成功记录 |
| 固定业务/网络、受控条件、采集策略配置 | configs/baseline、scenarios、observation | 尚未移动；保留值、生成规则及原配置摘要 |
| results/e1 的正常探索 | archive/initial_screening 的结果分区 | 尚未移动；旧全文和机器结果继续可读 |
| results/e2 内的 C1、网络案例、离线边界 | archive/sender_queue、network_cases、operation_boundary 的结果分区 | 尚未移动；共享证据保留单一原件并链接，不重复复制 |
| 新的三类问题和采集成本结果 | results/local_service、backpressure、multipath、measurement_budget | 在实际产生内容时建立，不预建空结果 |
| 原 runs 中的来源、归档、切片和大日志 | 保持原位置，由清单显式引用 | 不搬迁、不删除，不复制进 Git |

迁移验收要求见[现行方案第 10 节](../plan.md#10-目录文档和迁移要求)。先保存小型检查起点，进行目录重构，核对导入、帮助、链接和逐项分析结果，再开始新研究代码。旧模块不用大量兼容包装维持；历史运行通过固定版本复现。

[initial-plan]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/plan_v0.1.md
[boundary-plan]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/candidates.md
[old-method]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/method.md
[old-usage]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/usage.md
[old-conclusions]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/conclusions.md
[old-task]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/docs/tasks/current.md
[e1]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/results/e1/README.md
[e2]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/results/e2/README.md
[network-result]: https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/results/e2/network-discovery-verification.json
