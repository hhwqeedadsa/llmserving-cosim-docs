# 当前任务与交接

**平台 v0.5 的 P 部分（M18～M21）已完成；E 部分执行中，状态 `in_progress`。** 用户现已授权连续完成 E 部分，按实验方案的证据与结束条件自主推进，验证后本地提交，不推送。平台基线固定为 `e3db37a`，实验与平台分别记录、实施和验收。

## 依据与文档分工

| 文件 | 职责 |
|---|---|
| [v0.5 设计档案](../archive/moe_ub_cosim_dev_v0.5.md) | 保留通信基线、资源与等待、内部事实、有限层段及平台验收要求 |
| [用户指南](../user_guide.md)、[A3 示例](../../examples/a3_segment/README.md) | 普通用户的准备、运行、记录、保存、采样和回放操作 |
| [M21 验收](../../fixtures/m21_platform/README.md) | 实际输入、测试结论、资源、失败原因及完整日志入口 |
| [问题筛选实验方案](../../experiments/scaleup_issue_screening/plan_v0.1.md) | 后续正常输入、候选选择、成因与稳健性、测量价值及结束条件 |
| [实验入口](../../experiments/scaleup_issue_screening/README.md) | E 部分的方法、后续真实操作及结果入口 |
| 本页 | 当前范围、阶段状态、证据入口及停止位置 |

通用维护和 Git 约束继续见 [AGENTS.md](../../AGENTS.md)与[开发维护](../development.md)。平台和实验分别实施、提交与验收，不以实验取得正收益作为平台完成条件。

## 起点与已完成范围

2026-10-09 从交接前基线 `c8b4b670c4802852ebb9b7a0ddb106a08881e2be` 快进同步到 `48f6b00952689c383be818efb6d9af4b981e872e`，起点工作区干净，13 个依赖和八份固定 CANN 来源均核验通过。同步证据在 `runs/v05-handoff-review-20261009T113317Z/`。原实验、原模型条件下的全 rank 发送配额与 LOAD 拆分结果保留，见[上一阶段研究总结](../../experiments/scaleup_observability/conclusions.md)。

本轮采用 A3/CANN 8.5 固定来源和独立 STORE/STORE 资源模板，区分通信核、局部缓冲、下层有限请求池以及数据、状态与协议回收事件。公开有限层段入口保留完整部署容量、原始层号、真实路由来源和上下文；从明确初态执行，不构造任意中途快照。完整 HBM/缓存、A5、多 TP、全量硬件校准和完整 64 轮服务未纳入本次验收。

## 阶段状态

| 阶段 | 状态 | 结果与证据 |
|---|---|---|
| M18（P1）来源与执行条件 | `completed` | 八份来源逐项核对、分核/缓冲/事件对应与假设明确；[来源记录](../../fixtures/m18_a3_execution/README.md)，提交 `a48e1c7` |
| M19（P2）资源与关键依赖 | `completed` | 独立资源、真实完成事件、部分准备与 Token 归约、工作量守恒、新旧路径及两轮回收通过；[小型验收](../../fixtures/m19_a3_resources/README.md)，提交 `6ddb0c0` |
| M20（P3）内部事实与有限层段 | `completed` | 公共准备、运行、事实导出、保存与离线回放，范围拒绝和旧用户流程通过；[流程证据](../../fixtures/m20_segment_facts/README.md)，提交 `1bb02a0` |
| M21（P4）平台独立验收 | `completed` | 四类小测试、采集启停、来源绑定、128/1024 请求三层执行及目标包全文件校验通过；[最终验收](../../fixtures/m21_platform/README.md)，代码检查点 `a0af1d7`，128 请求证据检查点 `e4bfd65` |
| E1 正常业务探索 | `in_progress` | 方法和四组输入已固定；256 请求短段完成、校验并归档，512 请求执行中，1024 请求短段准备中；[方法](../../experiments/scaleup_issue_screening/method.md)，[首组摘要](../../experiments/scaleup_issue_screening/results/e1/g0-b2-short.json) |
| E2 成因与稳健性 | `conditional` | E1 有可信候选后，最多深入两个，先记录选择理由 |
| E3 测量价值 | `conditional` | E2 已有可信解释与具体判断歧义，再比较信息 |
| E4 完整场景与收尾 | `conditional` | 有通过前述检查的候选才跑少量完整首轮；没有候选可按已完成范围结束 |

## 最终检查与使用边界

M21 的四类小测试覆盖独立核与缓冲复用、Token 分别到齐、共享出口竞争和晚准备；采集开关保持已比较的完成记录与实际包字节/时间摘要。新旧公共流程、C++ 计划范围拒绝和来源绑定回归均通过，详细证据按上表分别保存。

目标验证使用 128 rank、256 专家、Top-8、隐藏维度 7168、完整物理网络及 61 块部署容量，只执行原始第 3～5 层。1024 请求实际执行 973728 个节点、193644 次网络传输及 1308 次同芯片交付，模拟结束于 280072 ns；字节、回调、资源释放和网络排空核对通过。运行阶段采样进程组 RSS 峰值约 8.42 GiB，累计产物峰值约 43.65 GiB，未超过既有上限。具体预算、阶段耗时和采样限制见 M21 验收。

真实路由准备、范围与有效输入见 `runs/m21-platform-acceptance/scale-1024-prepare/results/`；运行与导出在 `scale-1024-run/`；独立核算在 `scale-1024-verify/`。可复用包为 `scale-1024-slice/`，4543 个文件通过摘要及硬链接核验，保存证据在 `scale-1024-freeze/`。这些目录均位于 `runs/m21-platform-acceptance/` 下，来源数据、构建和大型产物不提交 Git。

核数、复制栅栏与计算成本保留明确参考假设，详见[模型依据](../model_validation.md)。三层从空网络开始，不视为稳态；真实轨迹来源不等于精度匹配或目标硬件校准。本轮未重复目标规模后端回放，回放等价由小型独立流程验证。

## 当前执行位置

P 部分已完成，代码、输入、结果摘要及按职责维护的文档在本地保存；未推送。ASTRA 与 LLMServingSim 修改已由内向外本地提交，主仓 gitlink 和 `deps.lock.json` 一致，未升级上游；实际依赖提交与二进制摘要见[M21 摘要](../../fixtures/m21_platform/scale-summary.json)。

E1 首组完成 568544 个节点、121880 次逻辑传输，模拟结束于 110792 ns；相关 2688 个交换出口均有观察。完整记录、切片和清单已无损归档，展开文件释放，分析保留在 `runs/scaleup_issue_screening/analysis/g0-b2-short/`；[资源摘要](../../experiments/scaleup_issue_screening/results/e1/g0-b2-short-resources.json)记录实际耗时和采样峰值。512 请求执行中，1024 请求短段并行准备；联合仿真串行，之后仍按预定顺序完成短段及长段。

独立合成小流程、六项工具检查及恢复后小记录的计算时序分析通过，见[工具证据](../../experiments/scaleup_issue_screening/tooling-summary.json)、[分析补充检查](../../experiments/scaleup_issue_screening/analysis-check-summary.json)和[用户操作](../../experiments/scaleup_issue_screening/usage.md)。已有源文件不足以组成第二组不重复的 1024 请求，本轮不下载、不循环来源，也不将相关 rank 或同一运行的回放计为独立重复。累计产物仍受 48 GiB 限制，旧产物保留。实际命令和退出记录位于 `runs/scaleup_issue_screening/`。根据 E1 结果至多选择两个候选，只有满足成因和信息比较条件才进入后续阶段，不要求正收益。
