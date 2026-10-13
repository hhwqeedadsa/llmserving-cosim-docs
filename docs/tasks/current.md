# 当前任务与交接

继续既有 **Scale-Up 正常业务问题筛选**。本轮预算内可执行的筛选、辅助档实际等待核验、一次定向服务对照及候选去留已经收尾。**整体状态为 `partial`：1024 请求发现档的逐包与因果业务验证尚未执行，不能把辅助档结论当作该档完成验收。** 用户随后授权的只读磁盘核算已完成；建议累计上限 56 GiB，具体新额度尚未确认，配置仍为 48 GiB。不新建任务或里程碑，不自动继续其他候选、正式 E3/E4 或算法开发。

已从干净工作区同步远端 `main` 至 `840315c`。原 C1 发送队列分支冻结，未增加 worker、额度、状态优先级或核内同步实验。ns-3-UB 保持 `13cf3da346455185b3777322944fb1d3c39e0d3d`，其余依赖保持 gitlink 与 `deps.lock.json`；13 项依赖初检通过，收尾递归工作区无改动。后续实现只有本地提交，未经新的明确授权不推送。

## 目标与入口

从已有正常执行筛选真实交换网络等待，核对必要数据、状态、完整完成依赖及后继计算，以有限定向服务对照检验业务作用。只有机制与业务影响均合格时才进行初步受限信息比较；没有合格案例也可按实际覆盖给出去留，不强求正结果。

本轮要求保存在[候选筛选与核验](../../experiments/scaleup_issue_screening/candidates.md)，整体问题见[实验方案](../../experiments/scaleup_issue_screening/plan_v0.1.md)，固定输入见[方法](../../experiments/scaleup_issue_screening/method.md)。维护操作沿用 [AGENTS.md](../../AGENTS.md)及[性能现象核查](../development.md#性能现象核查)。

## 完成范围、结果与证据

| 内容 | 状态 | 结果及证据 |
|---|---|---|
| 原 M18～M21、E1/E2 与 C1 | `completed` / 冻结 | 保留[原结果](../../experiments/scaleup_issue_screening/results/e2/README.md)与[组织效应](../../experiments/scaleup_issue_screening/results/e2/c1-organization-effect.json)，不重开清单 |
| 四份正常执行的覆盖与网络筛选 | `completed` | 每份 2688 出口；701573 个相邻正面积窗口组，不能解释成独立或连续拥塞。先固定规则，再选 N1/N2；见[筛选摘要](../../experiments/scaleup_issue_screening/results/e2/network-screening.json) |
| 1024 请求发现档的完整业务条件 | `completed`，因果验证受限 | 已复核第 7、8 层全部实际最晚端及候选接收端的状态、数据、重排、归约与资源依赖；尚不能由聚合窗口证明具体包在该出口等待 |
| 原 256 请求辅助出现的合并补采 | `completed` | 34 个详细端口、0～40 μs；原执行记录及全部 2688 个端口指纹等价；FIFO 逐包等待与独立队列积分一致 |
| N1 定向服务及完整依赖反证 | `completed` | 802:6 在 20～40 μs 从 400 提高到 800 Gb/s；到达至 FIFO 最大间隔从 811 降至 93 ns，部分完整数据提前数百 ns，但全部配对操作及最后模型计算只提前 8 ns（0.0072%）；见[服务对照](../../experiments/scaleup_issue_screening/results/e2/network-service-effect.json) |
| N2 核验 | `completed`，限辅助档 | 最后状态在选定出口仅 1 ns 分配、0 ns FIFO 等待；没有另加服务对照的业务作用依据 |
| 发现档的实际路径及服务效应 | `blocked`，预算 | 1024 请求重放输入的非执行部分即超过剩余额度；没有执行该档补采/对照，不以 256 请求结果替代 |
| 累计磁盘核算与重放空间预测 | `completed` | 去重盘点全部 16 个既定根目录，核验三份归档；保留所有旧产物时，新增 1/2/4 次串行核查的累计峰值预计 50.32/50.70/51.45 GiB。建议 56 GiB，未修改限额或启动仿真；见[核算依据](../../experiments/scaleup_issue_screening/results/e2/network-disk-forecast.json) |
| 初步信息比较 | `not_entered` | 已核验辅助出现为低影响参照；发现档尚未合格。未比较受限算法，不宣称观测足够或映射无价值 |
| 正式 E3/E4、P4 与测量算法 | 未开始 | 不在本次授权范围 |

[候选去留与紧凑证据](../../experiments/scaleup_issue_screening/results/e2/network-disposition.json)、[E2 解释](../../experiments/scaleup_issue_screening/results/e2/README.md#逐包等待定向服务与候选去留)和[综合结论](../../experiments/scaleup_issue_screening/conclusions.md)给出完整判断。N1/N2 在已核查辅助档中保留为低影响参照；1024 请求发现项限定保留，既未验证、也未否定。长段同位置 N1 最高窗口均值明显低于短段，进一步限制稳定瓶颈解释；相关输入不是独立重复。

## 平台与实验验收

新增平台能力仅为公开 `run/replay` 的 `diagnostic_storage=gzip`：后端退出后，对当时留下的文本诊断日志无损压缩、完整读回校验后释放文本副本。没有改变后端二进制、请求、计算、网络或时序。4 项平台检查及不依赖旧产物的公开运行、冻结、离线重放、采样与验证流程通过，见[平台验收补充](../../fixtures/m21_platform/diagnostic-storage.json)；平台本地提交为 `49005b8`。

实验筛选检查点为 `d75406d`，实验程序另行保存。10 项网络分析/配置小测、此前 22 项相关工具回归、全新合成 A3 被动补采/定向服务流程、目标补采等价及服务运行完整性核验均通过。分析和摘要读取错误只重做分析，没有据此重跑目标仿真。用户入口及经过验证的命令见[操作说明](../../experiments/scaleup_issue_screening/usage.md#正常网络侧筛选)。

## 资源、保存与例外

新增目标后端运行 **2/6 次**：一份合并被动补采、一次 N1 服务对照。失败的空间预检没有启动后端，不占新的执行次数。两次目标后端和导出均在原监控预算内完成；不得将这句话扩写成所有收尾步骤都满足磁盘上限。

上限保持：后端 RSS 48 GiB、地址空间 56 GiB、7200 秒；其他受监控阶段 8 GiB、12 GiB、1800 秒；累计产物 48 GiB、最低空闲 20 GiB。启动累计约 47.253 GiB。原服务预检为 48.071 GiB，因而增加了已验收的日志无损压缩后再执行，没有提高额度。

**收尾存在一次资源处理错误**：把约 145 MiB 的完整生效对象表当作小元数据重复复制，归档预检测得累计约 **48.038 GiB**。当时没有启动归档或新仿真；核对源和副本摘要后立即释放刚生成的重复副本，原表保留于完整运行及随后核验的归档。错误、处理与修正后预检均保留，不隐去这次超限。归档后累计实测约 **47.491 GiB**。完整阶段时间、空间实测和保存清单见[资源记录](../../experiments/scaleup_issue_screening/results/e2/network-resources.json)。

本轮所有大文件和详细日志位于 `runs/scaleup_issue_screening/network-screening-20261013/`。`capture.tar.gz`、`service-n1.tar.gz` 及同名 `.json` 保存两次完整执行与逐文件摘要，核验后释放各自展开副本；没有删除旧归档。`capture-gates/`、`service-gates/`、`capture-packets/`、`service-packets/`、`recurrence.json` 和 `service-effect-with-hops.json` 可直接读取，不需要展开完整执行。命令与退出状态见 `commands.jsonl`、各阶段 `probe.json`，次数见 `target-runs.json`。

## 停止位置与最小缺项

要完成原 1024 请求发现档的因果验收，最小缺项是其原图重放所需空间、与原执行等价的限定补采，以及有业务线索时的服务对照；不是追加端侧建模或算法。只读核算确认当前累计约 47.492 GiB，所在文件系统空闲约 5.868 TiB，限制来自旧累计额度。建议保留旧产物，把累计上限改为 56 GiB；全部既定根目录仍计费，内存、时间、最低空闲及已用 2/6 次目标运行额度不变。

本次只完成核算，未删除或压缩替换旧文件，未修改预算、输入和平台，未新增仿真。核算实测约 170.44 秒、采样 RSS 225.16 MiB；逐文件盘点、完整压缩计数、重复摘要和归档核验在原运行目录的 `disk-*.json`，实际命令及退出状态见 `disk-accounting-resources/probe.json` 与 `commands.jsonl`。[资源说明](../../experiments/scaleup_issue_screening/results/resources.md#磁盘核算与原发现档重放预测)区分实测、预估及整理候选。空间建议尚待确定；研究范围不变，不自动进入 E3/E4，不推送后续实现。
