# 设计档案与历史记录

这里保存方案沿革及原始过程记录的入口。日常使用从[用户指南](../user_guide.md)开始，当前实现按[系统架构](../architecture.md)、[后端参考](../../backend/README.md)、[模型依据](../model_validation.md)和[输出数据](../record_storage.md)阅读。

2026-10-05 的文档整理以 `e20d42a2bcf0b2ac2529b8641a9492638ffc786f` 为基线。整理只改变文档组织和表达，源码、测试输入、配置、依赖指针及证据文件保持不变。旧规格的技术正文保留，已有实验的失败和中间状态仍可追溯。v0.2 的路径和 SHA-256 由依赖锁文件核对，因此它保留在原位置且逐字节不变，不另存重复副本；这不表示它重新成为日常使用入口。v0.3 移至本目录，并增加历史导读。

## 版本化设计

| 文件 | 历史作用 | 阅读时注意 |
|---|---|---|
| [开发规范 v0.2](../moe_ub_cosim_dev_v0.2.md) | 规定联合执行、观测与 M0 至 M5 的分阶段开发 | 包含初始用例限制、接口草案和当时未实现项 |
| [增量规范 v0.3](moe_ub_cosim_dev_v0.3.md) | 在 v0.2 上增加参数化、连续组批、真实输入、CANN 和 M6 至 M10 | 包含各次批准的增量，最终收尾改为完整首轮；并非全部 schema 的统一版本号 |
| [增量规范 v0.4](moe_ub_cosim_dev_v0.4.md) | 原生 STORE/LOAD、逐包喷洒、准备成本、出口服务、等待统计和聚合保存，组织为 M11 至 M16 | 保留原要求及用户批准的不新增多 TP 范围；正文的待实现表述是当时状态，进度见当前任务 |

未被后续批准修订的行为约束继续保留。现行文档将这些要求按主题解释，具体字段与命令按当前实现核对；旧规范中的配置草案不应直接作为当前可执行输入。若发现语义冲突，应说明原要求与现行实现的差异，不能因为文件归档就视为旧要求已取消。

## 原要求到现行文档的对应

| 原章节 | 主题 | 当前阅读位置 |
|---|---|---|
| v0.2 §0–1；v0.3 §1–2 | 研究用途、范围与基本概念 | [项目介绍](../../README.md)、[架构](../architecture.md)、[场景](../scenarios/cm384_r1.md) |
| v0.2 §2、11、14 与附录 A | 固定依赖、目录、实现位置与协作 | [开发维护](../development.md)、[后端参考](../../backend/README.md)、[依赖锁文件](../../deps.lock.json) |
| v0.2 §3、5、7；v0.3 §3、4.5 | 状态职责、图依赖、同一时间与调度反馈 | [架构](../architecture.md)、[后端参考](../../backend/README.md) |
| v0.2 §4；v0.3 §4.1、4.4 | 输入、身份、放置、图与路由 | [架构](../architecture.md)、[后端参考](../../backend/README.md)、[模型依据](../model_validation.md) |
| v0.2 §5.1、9.3；v0.3 §4.2–4.3 | 连续组批、逐请求进度与 KV 容量 | [架构](../architecture.md)、[模型依据](../model_validation.md) |
| v0.2 §6、8；v0.3 §3.2、4.6–4.7 | 通信行为、完成事件、网络参数与本地交付 | [模型依据](../model_validation.md)、[后端参考](../../backend/README.md)、[参考场景](../scenarios/cm384_r1.md) |
| v0.2 §9；v0.3 §4.8 | 计算、路由、容量的来源与有效范围 | [模型依据](../model_validation.md)、[参考场景](../scenarios/cm384_r1.md) |
| v0.2 §10；v0.3 §3.6–3.7 | 端侧与端口观测、时钟、字节、评价数据 | [输出数据](../record_storage.md)、[后端参考](../../backend/README.md) |
| v0.3 §4.9 | 保存首轮、离线采样与后端重放 | [用户指南](../user_guide.md)、[架构](../architecture.md)、[输出数据](../record_storage.md) |
| v0.4 §3–6 | 原生请求、逐包选路、端侧成本和有向出口服务 | [后端参考](../../backend/README.md)、[架构](../architecture.md)、[模型依据](../model_validation.md) |
| v0.4 §7–9 | 操作阶段、队列等待、聚合与详细保存、采样和重放边界 | [输出数据](../record_storage.md)、[用户指南](../user_guide.md)、[原生示例](../../examples/native_first_round/README.md) |
| v0.4 §10 | 分项、公开流程及完整原生首轮验收 | [里程碑](../milestone_history.md)、[最终证据](../../fixtures/m16_native_target/README.md) |
| v0.2 §12–13；v0.3 §5–6 | 具体用例、分阶段与最终验收 | [里程碑](../milestone_history.md)、对应 `fixtures/` 说明和证据文件 |
| v0.2 §14；v0.3 §7 | 任务、提交、记录与可复用交付 | [开发维护](../development.md)、[AGENTS.md](../../AGENTS.md)、[当前任务](../tasks/current.md) |
| v0.2 附录 B | 当时使用的资料和源码定位 | 保留于 v0.2 原文；当前模型来源详见[模型依据](../model_validation.md) |

规范中的手算用例与精确历史验收条件仍由原文和对应 fixture 保存，没有将它们替换成笼统的“支持”。本次不修改它们引用的 JSON、YAML 或检查程序。

## 历史记录

以下固定链接指向整理前的原文。它们保存过去的事实和决策，不继续追加进度；新的任务结果按[开发维护说明](../development.md)保存。原文中的 `runs/` 和本机路径表示当时的证据位置，不表示这些大文件随 Git 仓库分发。

| 原文件 | 保留的内容 | 当前替代入口 |
|---|---|---|
| [environment.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/environment.md) | 2026-09-27 至 10-05 的环境、命令、失败、恢复及推送记录 | 安装看[用户指南](../user_guide.md)，检查规则看[开发维护](../development.md)，阶段结果看[里程碑](../milestone_history.md) |
| [bootstrap.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/bootstrap.md) | 2026-09-27 空仓初始化、初始依赖和规范摘要 | [开发维护](../development.md)和[锁文件](../../deps.lock.json) |
| [user_delivery.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/user_delivery.md) | 统一用户命令与可复用交付的原始要求、接口草案及验收方案 | [用户指南](../user_guide.md)、[开发维护](../development.md)、[最终验收](../../fixtures/m10_research_slice/README.md) |
| [tasks/current.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/tasks/current.md) | M10 首轮交付的授权、过程、检查点和最终结果 | [当前任务](../tasks/current.md)只描述本次工作，M10 结果见[最终验收](../../fixtures/m10_research_slice/README.md) |
| [future_iterations.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/future_iterations.md) | F01 至 F04 的原始方向与 v0.3 安排 | [后续事项](../future_iterations.md)只保留未完成工作 |
| [target_readiness.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/target_readiness.md) | CM384 参考配置的旧缺项表及逐次就绪核对 | [CM384/R1 场景](../scenarios/cm384_r1.md) |
| [model_validation.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/model_validation.md) | DeepEP 起始核对与后续逐阶段模型记录 | [模型依据](../model_validation.md)按当前主题组织 |
| [milestone_history.md](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/milestone_history.md) | 整理前的完整里程碑长文 | [里程碑历史](../milestone_history.md)保留简短证据索引 |

旧证据 JSON 内的文档路径或当时提交列表保持原样，可用上述固定版本解析。压缩后的资源调查说明也保留各自历史原文链接，避免把早期资源不足或未完成状态误读为当前结论。
