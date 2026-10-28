# 重度丢包对照：事前协议

起点：本文件提交前的仓库 HEAD。所有条件、预期和预算在执行任何正式条件前冻结。

## 固定输入

- 服务：`examples/scaleout_rdma/service.json` 改 `parallel.groups=[[0,2,4,6],[1,3,5,7]]`、`model.layers=1`、`workload.decode_steps=2`，其余不变（batch/rank4、H1024/F4096/E16/K2、声明计算、route_seed=11）。
- 拓扑：`examples/scaleout_rdma/small-clos-100g.json` 生成的 100G 三层 Clos。
- 协议：`examples/scaleout_rdma/rdma.json` 加全节点包捕获（0–5ms，上限 100000 事件）。不启用 recovery/admission 扩展，恢复为原生语义。
- 目标消息：基线运行实际提交的首图（g0.w0）中 `phase=dispatch、src=0、dst=4` 的消息；其字节数 B 与捕获中实际 ACK 流共同决定规则序号。

## 条件与事前预期

| 条件 | 规则（均命中目标消息） | 预期退出 | 事前预期 |
|---|---|---|---|
| baseline | 无 | 0 | 全部完成 |
| data-57-transient | 数据序号 {0,2000,4000,6000}（7 包中 4 包，57.1%），各丢 2 次 | 0 | 成功；目标完成晚于基线；源端存在重复发送字节 |
| data-57-persistent | 同上一组序号，各丢至多 10000 次 | 非 0 | 5ms 内服务不完成；目标接收连续字节停滞；另一 EP 组两轮照常完成 |
| data-86-transient | 数据序号 {0,1000,2000,3000,4000,5000}（6/7，85.7%），各丢 2 次 | 0 | 成功；重复发送不少于 data-57-transient |
| ack-57-transient | 非最终 ACK 序号 4 个（约 57%），各丢 2 次 | 0 | 成功；目标确认时刻相对基线变化很小（累计 ACK 掩盖非最终丢失） |

事前判别规则：

1. 每条规则的实际命中次数记录在 `fault_injection.drop_counts`；transient 条件必须恰好等于声明次数，否则该条件无效并保留证据。
2. 故障组停滞后不会提交的未来图不补造；只逐字节比较两边都实际提交的共同图。
3. 若 baseline 失败或目标消息/ACK 流与上述定义不符，停止全部后续条件，保留现场。
4. ACK 选择排除最终 ACK（最终 ACK 持续丢失在原生模式下的停滞已由 scaleout_faults 记录）。

## 批次 A 偏差记录（2026-10-28）

批次 A（原生恢复语义）只完成 baseline 与 data-57-transient 两个条件。data-57-transient 四条规则各命中恰好 2 次后，目标消息（id 1，6144B）接收连续字节停在 0，源端累计发送恰为 2×6144B 后无第三次尝试；整个服务在 5ms 仿真上限处非零退出，与事前"成功"预期不符。按规则立即停止其余三个原生条件，证据保留在 `/dev/shm/moe-heavy-loss-20261028/runs/`，不重跑、不改预期。

偏差解释（执行后核对，非事前声明）：原生模式下第二轮重复丢包后没有新的 NACK 触发源端动作，且没有有界超时兜底——正是 communication_fragility.md 记录的"尾包/最终 ACK 丢失后没有新包触发 NACK"脆弱点。

## 批次 B（在看到批次 A 证据后登记）

同一输入、同一目标消息、同一四组丢包规则，仅协议增加平台既有可选项：
`recovery={mode: go_back_n, initial_rto_ns: 40000, max_rto_ns: 160000, max_retries: 3, max_events: 100000}`。

事前预期：

- baseline（含 recovery 配置）：成功，无丢包时行为与批次 A 基线一致。
- data-57-transient：成功；目标消息完成；源端出现重复发送；完成晚于基线约 1–2 个 RTO 量级。
- data-86-transient：成功；重复发送不少于 57% 条件。
- data-57-persistent：非零退出且 `failure.reason == recovery_retry_exhausted`（有界显式失败，而非 5ms 静默停滞）；另一 EP 组两轮照常完成。
- ack-57-transient：成功；累计确认掩盖非最终 ACK 丢失。

批次 B 的"预期"是在已知批次 A 结果后登记的，不能作为独立的事前预测证据；它的作用是有界恢复与原生语义的对照。

- 单条 CLI 90 秒墙钟；整轮包含 1 次拓扑、5 次 rdma-run、4 次 rdma-diagnose。
- 运行目录累计 128MiB；/dev/shm 预留 8GiB；超出立即停止，不扩容。
- 零编译、零下载、零依赖修改、不 push。构建为独立预算的既有 `rdma-build` 产物，仅引用其二进制。
- 原始运行留在易失卷；提交 Git 的只有本目录程序、文档和小型结果摘要。
