# 当前任务与交接

既有 **Scale-Up 正常业务问题筛选 / C1 的 E2 增量机制核验**已完成，状态 `completed`。按用户要求同步远端 `main` 到 `c2e6389` 后继续原任务；没有新建任务、里程碑或实验，没有进入 E3/E4。本次实现和实验只建立本地提交，未推送。

结论是：**请求额度和跨核队首推进显著影响当前模型，但真实 A3 的队列组织及核内同步对应尚未证实，因此限制 C1 的现实解释，暂不据此开展新增测量研究。** 这不表示端网映射已被证明有收益或无收益。研究依据见[候选 C1](../../experiments/scaleup_issue_screening/candidates.md#7-本次核验后的候选判断)、[E2 增量结果](../../experiments/scaleup_issue_screening/results/e2/README.md#原生请求与核内同步增量核验)和[研究结论](../../experiments/scaleup_issue_screening/conclusions.md)。

## 范围与状态

| 内容 | 状态 | 结果入口 |
|---|---|---|
| 平台 P：M18～M21 | `completed` | [原平台验收](../../fixtures/m21_platform/README.md)，不重开清单 |
| E1 正常业务探索 | `completed` | [E1 结果](../../experiments/scaleup_issue_screening/results/e1/README.md)，原输入及执行事实保留 |
| E2 原对照与交付补证 | `completed` | [E2](../../experiments/scaleup_issue_screening/results/e2/README.md)、[原交付复核](../../experiments/scaleup_issue_screening/results/delivery-review.md) |
| E2：C1 发送推进机制核验 | `completed` | 两条原链、实际配置、请求阻塞、固定源码同步关系及唯一 32→64 额度对照 |
| E3、E4 | `pending` / `conditional` | 未执行，不在本次授权范围；不自动启动 |

固定原真实路由、128 rank、256 请求、原始第 6～8 层、物理网络、放置、A3 STORE/STORE 和线性计算成本。没有重开负载代表性或成本校准，没有改变核内同步，也没有用 `ThreadNum=48` 代替单因素并行度对照。原平台、原归档和历史检查结果没有改写。

## 结果与验证

- [原记录复核](../../experiments/scaleup_issue_screening/results/e2/c1-initial-review.json)：第 7 层 `100+5732+6188=12020 ns`，第 8 层 `100+8177+4306=12583 ns`；实际对象为 1 worker、STORE 额度 32、共享请求池 256。单 worker 为项目显式设置，没有找到硬件映射依据。
- [被动补采等价](../../experiments/scaleup_issue_screening/results/e2/c1-capture-equivalence.json)：原执行、公开观测和全部 2688 个出口的包内容/发送时刻指纹保持。15344 条队列记录完整；[八段等待](../../experiments/scaleup_issue_screening/results/e2/c1-native-review.json)均有 worker 额度占满证据，共享池未耗尽；入队时前方有 50～69 个跨核任务，具体请求及释放响应已保存。
- [额度对照](../../experiments/scaleup_issue_screening/results/e2/c1-quota64-effect.json)：仅改 STORE 额度 32→64，所选端点 4019 个实际请求范围一致，全图计划与计算时长一致。两条链缩短至 8578、8352 ns；实际最晚消费者变为 rank 33、56；三层结束从 110792 降至 97946 ns（−11.59%）。实际端口分布改变，总效应含网络反馈，不是硬件优化结论。
- [固定源码核查](../../experiments/scaleup_issue_screening/results/e2/c1-source-review.json)：CANN 文件摘要、选中 A3 分支及官方 API 已核查。数据/状态顺序和缓冲复用有依据；所有核共用一个 worker、下一份准备等待上一状态全部请求形成的现实对应仍缺依据。没有删除依赖来迁就结果。
- 平台最小检查通过：启停等价、边界与溢出、请求占用/释放守恒、worker 额度与共享池分支区分、冻结重放、额度白名单和固定拆分/编号池拒绝。STORE/LOAD 与 A3 回归通过。全新公开示例 `resolve → run → validate` 通过，所选端点 54 个 STORE、36 个 LOAD 请求各自形成/释放守恒。
- [13 项实验工具检查](../../experiments/scaleup_issue_screening/results/e2/c1-tooling-checks.json)、13 项依赖检查及差异检查通过。分析中的分块顺序变化、SEND 提交与完成区别及消费者选择错误已纠正并复核；失败与拒收摘要保留，没有重跑成功的额度仿真。

平台提交为 `8fe4884 feat(observation): capture bounded native request queue truth`、`4e23dda feat(replay): allow isolated native STORE quota overrides`；ns-3-UB 子模块先保存为 `fd1c6f0`，再更新主仓 gitlink 和锁文件。初始实验复核为 `cb4fe19`，本次实验收尾另以 `experiment:` 提交，与平台分开。LLMServingSim、ASTRA-sim 及更深依赖未改。

原平台仍为 `e3db37a67ebb13edd5dd522dd1f8028cc46c2e19`，原二进制 SHA-256 为 `ad7e83e5b19201e98dd0485c1ed05c0e58181f8f7604871ab1b59aeca0eb57bf`；本次补采与额度对照二进制共用 SHA-256 `9f8b0ac3f695a72806054478c96694baa73c85ecb9227534c805f9058d4d654a`。新旧证据分别绑定版本。

## 资源与复用

上限保持：后端 RSS 48 GiB、地址空间 56 GiB、7200 秒；其他阶段 RSS 8 GiB、地址空间 12 GiB、1800 秒；累计产物 48 GiB，最低空闲磁盘 20 GiB。没有提高限额或触发完整首轮、61 块或 64 步运行。原事实导出预算未提高。

[资源与检查摘要](../../experiments/scaleup_issue_screening/results/e2/c1-resources.json)保存实际命令和日志入口、资源峰值、失败记录及归档摘要。本次两次目标后端分别约 228、229 秒，峰值 RSS 均约 5.51 GiB；各自导出约 95 秒。累计产物采样峰值约 47.98 GiB，校验归档并释放本次恢复和展开副本后约 46.32 GiB；原归档保留。

本地增量根目录为 `runs/scaleup_issue_screening/c1-mechanism-20261012/`：

- `capture.tar.gz`、`quota64.tar.gz` 及相邻 `.json`：基线补采、额度对照完整运行和详细队列证据，均已校验归档。
- `platform-validation.json`、`quota-replay-validation.json`、`validation-archives/`：成功及失败的小型检查；`user-example/summary.json` 是独立公开示例结果。
- `commands.jsonl`、各 `*-resources/`：实际命令、工作目录、退出状态、资源与日志。分析重试只读取原记录，不算新仿真。
- `original.release.json`：原恢复副本的逐文件校验与释放范围，原 `archives/g0-b2-short.tar.gz` 未改。

用户入口见[实验操作](../../experiments/scaleup_issue_screening/usage.md#c1-原生请求队列核验)和[公开小例](../../examples/native_first_round/README.md)，无需依赖会话中的临时脚本。进度仅维护在本页；研究解释、实际结果和通用用法各归所属文档。

## 停止位置

本次已完成授权的机制核验与候选去留判断，停止于 C1/E2。若以后重新考虑该候选，最小缺项是选定 A3 实现可复核的状态到后续准备同步关系，以及 AIV/MTE 到协议请求队列的组织和共享范围；不是自动开始新任务的清单。后续研究和远端推送均等待新的明确授权。
