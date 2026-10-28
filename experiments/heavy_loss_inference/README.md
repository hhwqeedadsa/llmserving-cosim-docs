# 推理消息的持续重度丢包对照

研究问题：把单条 Dispatch 消息的数据包/ACK 包持续按 50% 甚至更高比例丢弃时，原生 RDMA 网络和执行中的推理服务实际表现是什么？具体区分：

1. 同一批序号每轮重传都被丢弃（持续 57%/86% 数据丢包），Go-Back-N 语义下的完成时间、重复字节和恢复过程。
2. 持续丢包不解除时服务如何失败，未受影响的另一 EP 组是否照常完成。
3. 同等比例的非最终 ACK 丢失是否被累计确认掩盖。

这是固定输入的行为对照，不是丢包模型校准、新恢复算法或收益实验。沿用既有公开 CLI（`scaleout topology/rdma-run/rdma-diagnose`），不改平台源码、不重编译。事前条件、预期与预算见 [protocol.md](protocol.md)；执行入口 [run.py](run.py)，独立离线复核 [analyze.py](analyze.py)。

## 运行

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python -B experiments/heavy_loss_inference/run.py \
  --backend <rdma-build 输出的 moe-rdma-service> \
  --protoc /home/lry/tools/protobuf/bin/protoc \
  --output /dev/shm/NEW_HEAVY_LOSS
python3 -B experiments/heavy_loss_inference/analyze.py \
  --input /dev/shm/NEW_HEAVY_LOSS --output /dev/shm/NEW_HEAVY_LOSS/analysis
```

输出目录必须不存在；单条 CLI 90 秒墙钟上限；目录 128MiB、卷预留 8GiB。故障条件中的 `data-57-persistent` 是事前声明的预期失败（非零退出），保留其原始结果，不重跑。

## 结果（2026-10-28）

交互网页（逐包时间线、完成时间与重传放大图）：[report.html](results/20261028/report.html)；文字报告 [report.md](results/20261028/report.md)；批次 B 数值表见 [analysis.json](results/20261028/analysis.json)，批次 A 保留证据见 [batch-a-preserved.json](results/20261028/batch-a-preserved.json)，目标消息逐包事件见 [timelines.json](results/20261028/timelines.json)。目标消息均为实际首图的 0→4 Dispatch（id 1，6144B，7 个数据包/7 个 ACK）。网页由 [render.py](render.py) 仅从本目录已提交摘要生成，不依赖易失运行目录。

| 条件 | 结果 | 关键数字 |
|---|---|---|
| 基线（原生/含恢复配置相同） | 完成 | 服务 54,362ns；目标确认 17,673ns |
| 57% 数据丢包×2轮，原生语义 | **失败**（批次 A，事前预期被推翻） | 两轮各丢 4 包后接收端不再发 NACK、源端无第三次尝试；接收连续字节停 0；服务空转到 5ms 上限 |
| 57% 数据丢包×2轮，go_back_n | 完成 | 目标确认 57,154ns（+39,481ns ≈ 1 个初始 RTO）；源端发送恰为 3× 全消息（18,432B）；服务 91,297ns（+68%） |
| 86% 数据丢包×2轮，go_back_n | 完成 | 与 57% 完全相同：57,154ns / 18,432B / 91,297ns——Go-Back-N 从首个缺口整体回退，丢 4 包与丢 6 包代价相同 |
| 57% 数据持续丢包，go_back_n | 显式失败 | `recovery_retry_exhausted` 于 444,203ns（约 40+80+160us 退避），源端发送 5× 全消息后停止；组 0 停滞、组 1 两轮照常完成 |
| 57% 非最终 ACK 丢包 | 完成 | 服务 54,362ns，与基线逐字段相同——累计 ACK 完全掩盖非最终确认丢失 |

主要观察：

1. **Go-Back-N 的丢包放大**：消息内任意位置丢包都触发从缺口起的整体重发，57% 与 86% 丢包的重传量、完成时间完全相同；代价由"丢包轮次数×RTO"决定，而不是丢包比例本身。
2. **原生 NACK 恢复的静默停滞**：同一 57%×2 轮丢包在原生模式下两轮后不再有任何恢复动作（全程只观察到 1 个 NACK），服务静默空转到 5ms 上限；有界超时把同一故障变成 444us 的显式失败或 +39us 的成功恢复。
3. **ACK 流的天然冗余**：57% 非最终 ACK 丢失对完成时间零影响；数据路径与确认路径的丢包敏感性完全不对称。

## 范围与局限

- 8 GPU、两组交错 EP4、H1024/E16/Top2、1 层 2 步 ready-KV Decode；计算为声明值，路由为固定合成选择，不是真实专家分布或硬件校准。
- 丢包针对从实际首图选出的 0→4 Dispatch 单条消息，是逐序号确定性规则，不是全网随机丢包率；"57%"指该消息 7 个数据包中 4 个序号在每轮均被丢弃。
- 持续丢包条件的"失败"只说明 5ms 仿真上限内未完成，不推断生产 RDMA 的必然行为。
- 原生模式不启用平台的 Go-Back-N 超时扩展；恢复完全由原生 ns-3 RDMA 的 NACK/重传语义决定。
