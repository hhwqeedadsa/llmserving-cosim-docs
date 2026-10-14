# 历史实验与迁移索引

本页区分已经结束的研究分支，并保留原始方案、方法、结果和运行入口。当前要求仅见[现行方案](../plan.md)。历史判断有各自模型与观察范围，不代表整个 Scale-Up 问题域已经被充分探索。

此次入口整理以主仓 `b345694fbc2faed8feebb425d43c60f765f7eee9` 为历史封存点。完整旧文档直接链接该提交，既保留原文和相对链接，也避免在新目录复制整套历史代码。下表同时给出本地文件的新位置；原始来源和版本继续以封存记录为准。

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

旧 results/e1、results/e2 已按下表迁移，机器摘要内容没有改变。[初始报告](initial_screening/README.md)、[跨分支核验全文](review.md)、[共享补证](delivery/README.md)及[历史资源](resources.md)在本地可读。初始计划和历次 candidates 的完整版本保留在上面的固定提交链接；过渡导航页已移除。

## 首批迁移对应表

本表记录实际路径对应，不另建迁移日记。原始记录与校验摘要保持，移动后的相对引用需修正；若旧文件内容被运行摘要绑定，保留原摘要和旧提交，另记新位置，不回写历史来源。

| 旧路径（相对实验根目录） | 新路径 | 内容约束 |
|---|---|---|
| 根目录非 `check_` 的 Python 模块 | `analysis/<原文件名>` | 只调整导入、模块名、ROOT 推导和资源路径 |
| 根目录 `check_*.py` | `tests/<原文件名>` | 保持测试含义及检查项 |
| 根目录 `*summary.json` | `tests/evidence/historical/<原文件名>` | 原始字节及摘要不变 |
| `configs/{backend-reference.yaml,fabric.json,layer-template.json,network-defaults.json,route-manifest.json,source-base.json,system.json}` | `configs/baseline/<原文件名>` | 原始字节及配置值不变 |
| `configs/{batch.json,delivery-pairs.json,controls/,check/}` | `configs/scenarios/<原相对路径>` | 原始字节不变；check 为小型输入 |
| `configs/{export-policy.json,facts.json,host.json,observation.json,record-policy.json}` | `configs/observation/<原文件名>` | 原始字节及预算不变 |
| `results/e1/*` | `archive/initial_screening/*` | JSON 原始字节不变；Markdown 仅修正引用 |
| `results/e2/network-offline*.json` | `archive/operation_boundary/<原文件名>` | 原始字节不变 |
| `results/e2/network-*.json`（上述离线文件除外） | `archive/network_cases/<原文件名>` | 原始字节不变 |
| `results/e2/*.json`（上述网络文件除外） | `archive/sender_queue/<原文件名>` | 原始字节不变 |
| `results/e2/README.md` | `archive/review.md` | 跨分支报告保留单一原件，仅修正链接 |
| `results/{delivery/,delivery-review.md,delivery-review-evidence.json,final-checks.json,resources.md,resources.json}` | `archive/<原相对路径>` | 共享补证不复制；JSON 原始字节不变 |
| `delivery_method.md` | `archive/delivery_method.md` | 旧补证方法仅修正链接 |
| `plan_v0.1.md`、`candidates.md` 过渡导航 | 本页固定版本入口 | 引用更新后删除过渡页；完整旧方案仍在 Git |
| `runs/` 的来源、归档、切片和日志 | 原位置 | 不搬迁，不改写历史来源 |

逐文件迁移映射、原始摘要及验收结果见 [迁移检查](../tests/evidence/migration.json)。该记录绑定迁移前 `cb9c116`；历史 JSON 中的旧代码路径、版本和摘要继续表示历史来源，不能当作当前模块路径使用。

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
