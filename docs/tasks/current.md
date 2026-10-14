# 当前任务与交接

继续既有 **Scale-Up 正常业务问题筛选**，本轮执行项为已有信息能力与通信结束边界的离线核验，状态 `completed`。研究判断、交付摘要及保存范围核对均已完成。任务起点同步远端 `main` 至 `7d351ce`；没有新建任务、里程碑、平台版本或实验目录。

## 目标、范围与决定

本轮按[离线核验要求](../../experiments/scaleup_issue_screening/candidates.md)，检查原理想映射增量能否由已有源端结束时间解释，比较完整关系与最后发送窗口，并按证据进入简单恢复及已有记录复核。约束沿用 [AGENTS.md](../../AGENTS.md)和[性能现象核查](../development.md#性能现象核查)。

**结论：有限保留“应用操作在特定设备侧出口的发送结束边界”，暂不推荐建设完整映射恢复系统。** 加强基线后原增量仍在；简单公开计数规则安全但没有恢复它。它能排除已经离开出口的旧阶段，不等于已经证明主要业务损失、在线可用性或真实硬件测量收益。详细判断见[研究结论](../../experiments/scaleup_issue_screening/conclusions.md)，完整解释见 [E2 离线信息核验](../../experiments/scaleup_issue_screening/results/e2/README.md#离线信息核验)。

## 验收状态与证据

| 内容 | 状态 | 结果及证据 |
|---|---|---|
| 原 M18～M21、E1/E2、C1 与 N1/N2 服务对照 | `completed`，保留 | C1 冻结；N1/N2 不追加 worker、额度、核内同步、带宽或其他服务实验 |
| 旧规则逐窗复算与源端完成语义 | `completed` | 228 窗口旧输入、关系、预测与评价逐项一致；1024/256 请求的 193576/120980 次应用网络传输和冻结图发送依赖全覆盖，无反例；[语义证据](../../experiments/scaleup_issue_screening/results/e2/network-offline-semantics.json) |
| 加强基线与完整/简化理想信息 | `completed` | 1024 请求仍 105/132→116/132；141 条完整关系化为 12 条边界，旧/加强基线的全量逐窗输出均等价；[1024 对照](../../experiments/scaleup_issue_screening/results/e2/network-offline-1024.json) |
| 简单离线恢复 | `completed` | 公开非零发送窗口与逐源时间范围的保守交集仍为 105/132；无过早上界或已知等待遗漏，保留具体失败原因；[恢复证据](../../experiments/scaleup_issue_screening/results/e2/network-offline-recovery.json) |
| 固定规则后的跨记录复核 | `completed` | 256 请求实际 80 窗口：加强基线/恢复均 36/49，理想边界 40/49；两种理想输入全量等价，无反例或遗漏；[256 复核](../../experiments/scaleup_issue_screening/results/e2/network-offline-256.json) |
| 小型分析验证及接口 | `completed` | 32 项合成检查通过；准备、判断、恢复与评价分开，已验证命令见[操作说明](../../experiments/scaleup_issue_screening/usage.md#已有信息与源端结束的离线核验) |
| 新后端运行、正式 E3/E4、完整测量算法 | 未执行，不在本轮范围 | 新增后端运行数 **0**；旧 4/6 计数的余额不作为本轮授权 |

两个请求规模各自的两个原选峰值在所有条件下已能唯一定位，不增加信息。真实混合、正队列但应用未知、缺失上游及采集尾部裁剪继续保留。256 请求历史上已被分析，又是嵌套来源，属于相关记录复核，不是独立留出集。理想边界使用未来封存记录，未宣称在线可得；简单恢复失败未推广为普遍不可识别。

## 输入、资源与保存

固定 A3 STORE/STORE、`data_ready`、请求划分、资源、计算成本、核内同步、路由和业务输入；没有平台或子模块改动。源端结束约束由公开模板语义统一决定，逐传输事实只核验，不用于挑选可见的有利样本。ACK、信用及协议回收不混入应用交付上界。

原目录 `runs/scaleup_issue_screening/network-screening-20261013/` 中保留：

- `offline-1024/`、`offline-256/`：按需恢复的分析源、语义审计、受限输入与旧规则复算。
- `offline-*-comparison/`：固定规则、简化边界、六组预测、逐窗原因与分开的 FIFO/部分上游评价。
- `offline-*-counters/`、`offline-*-recovered.json`、`offline-*-recovery-evaluation.json`：公开计数准备、独立恢复和私有评价。
- `offline-rule-freeze.json`、`offline-recovery-rule-freeze.json`、`offline-delivery-checks.json`：规则冻结与交付检查；实际命令、工作目录、退出状态和日志位置在 `commands.jsonl` 及 `offline-*` 日志。

旧 `discovery-information/`、原归档及切片保持不变，新结果分别绑定输入、代码与来源。分析采用 RSS 8 GiB、单进程地址空间 12 GiB、1800 秒，累计 64 GiB、原 16 个根目录、最低空闲 20 GiB。最高子进程内存高水位 3.457 GiB；累计空间约从 48.922 增至 49.017 GiB。未删除历史资料、增加预算或下载数据。详细资源单独维护在[资源汇总](../../experiments/scaleup_issue_screening/results/resources.md#离线信息核验资源)。

## 提交与停止位置

每批验证后建立实验提交：`5a14439` 源端语义与旧规则复算、`9084627` 加强基线与简化信息、`9e4f072` 简单保守恢复、`f294e3c` 跨记录证据与最终判断。没有平台修正或子模块变更需要单独发布。

2026-10-14 用户明确授权推送到 GitHub，本次发布范围为上述四批成果及本页授权记录，目标为 `origin/main`。远端核对、推送与提交一致性验证记录在原运行根的 `publish-offline-20261014-*` 日志及 `commands.jsonl`；原始归档和大型运行资料仍留在本地。

本轮交付完成后停止，不自动继续候选扫描、C1/N1/N2 服务对照、正式 E3/E4、事件标记或硬件机制。本次发布授权不延伸到后续新实现。
