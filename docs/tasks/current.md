# 当前任务与交接

状态：`in_progress`。2026-10-15，按用户要求审阅本地 `/home/lry/moe-ub-cosim`，与既有模型/Batch/Block 网络实验比较，调研并补充 Scale-Out。全部修改留在此仓库，验证后本地提交，**不 push**。

## 范围与次序

1. 先理解调度、构图、ASTRA、唯一 ns-3 时钟、事务完成、观测与真值隔离，再确定修改。
2. 按最新指令，Scale-Out 改为研究**非 UB 的 ns-3 / ns-3-alibabacloud**：核查 ASTRA 原生接口、NIC/rail/Clos 架构、RDMA 完成语义与接入范围。原 UB 分层功能保留为独立基线，停止将其作为 Scale-Out 实现路线。
3. 平台功能与研究性能结论分开。不把 UB 链路称为 RoCE，不以静态生成检查代替实际仿真；不同 ns-3 版本不得混链，也不将两个独立仿真相加冒充联合执行。

## 状态与证据

| 项目 | 状态 | 说明 |
|---|---|---|
| 整体架构与旧代码比较 | completed | 从 `dd7d8e8` 开始；[审阅报告](../reviews/scaleout_review_20261015.md) |
| UB 分层输入基线 | completed | 26 项相关单元检查、两个实际联合条件通过；服务器 100G/10G 时结束于 117098/146942ns，仅为 synthetic UB 功能验证。`runs/scaleout_validation_20261015/integration-02/summary.json` |
| 固定版本 UB 构建 | completed | 13 项依赖通过；新构建 `build/scaleout` 成功。工具链、峰值资源和失败尝试均保存在 `runs/scaleout_validation_20261015/`，gitlink 未改 |
| 非 UB Scale-Out 源码研究 | completed | 锁定 ASTRA 原生 ns-3 3.42、Alibaba 3.36.1 派生库；核查完成回调、CC 模式、NIC/rail 放置；[源码报告](../reviews/scaleout_ns3_backends_20261015.md) |
| 非 UB 实际探针与统一复现 | completed | 真实编译阿里 core 与 RDMA 所需模块；时间构造 1000 倍差异、8 条直连消息的发送回调缺失/重复已复现。统一脚本和版本化记录在 `experiments/scaleout_backend_review/`；完整日志 `runs/scaleout_ns3_review_20261015/` |
| 非 UB 在线服务后端 | pending | 研究完成后按独立可执行程序接入；当前不能用 `moe-ub run` 执行 RoCE，不声称已实现 |

预算：输入生成/单元验证时限 300 秒、地址空间 2 GiB、产物 256 MiB。独立构建限时 600 秒、RSS 4 GiB、地址空间 8 GiB、构建与本任务日志累计 1.5 GiB、磁盘预留 8 GiB、编译并发 2。后端按示例显式预算执行，不自动提高限额。完整 CM384/R1 不属于此次验收。构建/工具链记录：`runs/scaleout_validation_20261015/`。

非 UB 探针独立预算：配置 120s、构建 300s、每例执行 15s；RSS 2GiB、地址空间 4GiB，研究来源与构建累计 512MiB、磁盘保留 8GiB；短运行用更低内存上限。真实构建/运行完成不表示发送语义检查通过：结果明确保留 `all_send_callbacks_once=false`。尚未运行 Clos/rail 交换机拥塞对照，未接入非 UB 在线服务。

UB 平台基线已本地保存为 `5a3761f`。源码研究及探针另行本地提交；不 push。机器未配置 Git 作者，使用命令作用域 `Codex <codex@local.invalid>` 保存本任务提交，没有修改用户或全局 Git 配置。后续实现按非 UB 报告第 8 节从 RDMA 完成语义与网络微基准开始。

上轮 Scale-Up 筛选已完成，旧记录见 Git `dd7d8e8:docs/tasks/current.md` 与 [研究结论](../../experiments/scaleup_issue_screening/conclusions.md)，不重启其后续算法工作。
