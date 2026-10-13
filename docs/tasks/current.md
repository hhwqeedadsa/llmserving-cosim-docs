# 当前任务与交接

既有 **Scale-Up 正常业务问题筛选**本轮补齐工作为 `completed`。原 1024 请求发现档的被动补采、实际等待、完整业务依赖、一次 N1 服务对照及初步信息比较均已完成；候选去留有完整依据。没有新建任务或里程碑，没有重开 C1，没有进入正式 E3/E4 或测量算法开发。按持续授权建立本地实验提交，未经新的明确授权不推送。

## 目标、范围与入口

用户授权优先补齐原 `g0-b8-short` 发现档，完整保存证据，不为 56 GiB 做过多取舍。采用 **64 GiB 累计磁盘保护上限**，原 16 个计量根目录、内存、时间、最低空闲与六次目标运行上限不变。原真实路由、1024 请求、128 ranks、第 6～8 层、拓扑、计算成本、原生请求划分/资源及核内同步保持固定。

本轮执行依据是[候选筛选与核验](../../experiments/scaleup_issue_screening/candidates.md)及[性能现象核查](../development.md#性能现象核查)。原 M18～M21、E1/E2 和 C1 资料保留，发送队列分支冻结。原已同步远端 `main` 为 `840315c`；补齐前本地为 `381895c`。本轮未重新同步或推送，未修改平台及子模块；ns-3-UB 保持 `13cf3da346455185b3777322944fb1d3c39e0d3d`，13 项依赖检查通过。

## 完成结果与证据

| 内容 | 结果 | 证据入口 |
|---|---|---|
| 原四份正常执行筛选 | 各 2688 出口，保留 701573 个相邻正面积窗口组及预先选定 N1/N2；不把相关窗口当独立拥塞 | [原筛选](../../experiments/scaleup_issue_screening/results/e2/network-screening.json) |
| 原 256 请求辅助核验 | N1 局部等待明显下降，但三层最后计算仅提前 8 ns；N1/N2 均为低影响参照 | [历史去留](../../experiments/scaleup_issue_screening/results/e2/network-disposition.json) |
| 原 1024 请求被动等价 | 34 个端口、0～114 μs；8082 份原记录及全部 2688 端口内容/时刻指纹等价 | [发现档核验](../../experiments/scaleup_issue_screening/results/e2/network-discovery-verification.json) |
| N1 实际等待与业务作用 | 最后必要数据确实在所选出口等待；定向服务后最大到达至 FIFO 间隔 5556→417 ns，第 7 层最晚 Dispatch 提前 811 ns，三层最后模型计算提前 842 ns（0.213%）；完整追踪新的状态条件和最晚端 | [E2 解释](../../experiments/scaleup_issue_screening/results/e2/README.md#原-1024-请求发现档的完整核验) |
| 全 rank 与竞争解释 | 768 个操作、3840 个模型阶段全部配对并保留退化，计算时长不变；业务字节不变，反馈控制报文多 5 包/200 B；报告局部服务总效应，不称纯排队损失 | [发现档核验](../../experiments/scaleup_issue_screening/results/e2/network-discovery-verification.json) |
| N2 去留 | 所选出口的较大等待没有指向实际最后数据/状态或本核发送条件；保留低影响参照，不追加缺少业务依据的服务对照 | [完整依据](../../experiments/scaleup_issue_screening/results/e2/README.md#原-1024-请求发现档的完整核验) |
| 初步信息比较 | 两个峰值窗口基线均能定位第 7 层 Dispatch；其余 132 个有已知应用等待的相关窗口，加入一种理想操作—端口—发送窗口关系后，唯一定位 105→116，无已知等待遗漏；缺失上游、混合操作和未知项保留 | [信息比较](../../experiments/scaleup_issue_screening/results/e2/network-discovery-information.json) |
| 最终判断 | N1 有限保留为模型内服务影响案例及低优先级的“阶段交叠时出口等待归属”问题；尚不足以直接投入完整算法开发。N2 留作低影响参照 | [综合结论](../../experiments/scaleup_issue_screening/conclusions.md) |

初步比较有准备、受限判断、独立评价三个入口。理想关系没有泄露完成依赖、影响标签或干预结果；它尚不是已实现的映射恢复。新增归属信息不等于测得关键等待或可消除的业务时间，相关窗口不构成泛化验证集。实际 A3 请求组织、同步和硬件校准的既有限制保持，不为此追加 C1 实验。

## 验收、资源与保存

本轮仅修改实验读取、对照、信息检查工具与文档，没有平台修正需要另立提交。13 项网络工具小测通过；公开合成小型流程独立于旧产物，覆盖被动等价、服务对象回读、12 个端侧操作及 40 个模型阶段配对；补充字节分类守恒检查通过。原发现档执行、观测导出、等价与服务运行完整性分别核验。用户入口及经过验证的命令见[操作说明](../../experiments/scaleup_issue_screening/usage.md#原发现档的保存与有限信息比较)。

累计目标后端运行 **4/6 次**，全部完成：先前辅助档两次，本次发现档两次；没有为分析重跑后端，也未用满剩余次数。后端 RSS 48 GiB、地址空间 56 GiB、7200 秒；其他受监控阶段 8 GiB、12 GiB、1800 秒；最低空闲 20 GiB 不变。两次新后端约 347.32/364.69 秒，采样进程组 RSS 约 7.52 GiB。

累计磁盘从约 **47.492 GiB** 开始，监控最高约 **51.105 GiB**，核验归档后约 **48.915 GiB**。完整数据、旧归档及共享切片保留；只释放两份新增执行的展开副本和原归档的本轮恢复副本。历史 48 GiB 阶段误复制完整生效对象表导致约 48.038 GiB 预检超限的例外仍保存在[原资源记录](../../experiments/scaleup_issue_screening/results/e2/network-resources.json)，不以新预算掩盖。新阶段的墙钟、采样和操作系统子进程内存高水位、归档摘要见[本轮资源](../../experiments/scaleup_issue_screening/results/e2/network-discovery-resources.json)。

原目录 `runs/scaleup_issue_screening/network-screening-20261013/` 中：

- `discovery-capture.tar.gz`、`discovery-service-n1.tar.gz` 及各自 `.json`：逐文件核验后的完整执行。
- `discovery-slice/`：保留的可重放共享原图与输入。
- `discovery-*-gates/`、`discovery-*-packets/`、`discovery-service-n1-effect-complete.json`：完整依赖、逐包等待和全 rank 业务对照。
- `discovery-information/`：受限输入、先保存的基线、理想关系、独立评价及未确定项。
- `commands.jsonl`、各阶段 `probe.json`、`target-runs.json`：实际命令、工作目录、退出状态、资源与次数；部分原 case 内日志随完整归档保存。

当前停在本任务完整交付处，不自动增加候选、运行或后续阶段。后续是否进入新的验证或测量开发由用户决定。
