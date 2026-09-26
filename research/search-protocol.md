# 文献检索协议与覆盖边界

快照日期：2026-09-26  
对应账本：[literature_ledger.csv](literature_ledger.csv)

## 1. 研究问题

检索围绕四个问题展开：

1. AIDC 中的 LLM/MoE 推理系统实际测量了哪些对象和指标；
2. 服务、计算、collective、flow、packet、queue 各层已有何种优化；
3. 仿真或性能模型如何获得 workload、计算 profile 与真实网络流；
4. 哪些工作已经覆盖 EP、All-to-All、telemetry、collective control，避免重复提出“新”方案。

## 2. 时间与会议范围

主检索窗口为 2022–2026，理由是生成式 LLM serving、MoE 推理和 AI fabric 论文从这一时期开始
集中出现。纳入会议分为三组：

- 网络与测量：SIGCOMM、INFOCOM；
- 系统：NSDI、OSDI、SOSP、FAST；
- ML/HPC/体系结构与性能分析：MLSys、SC、ISCA、ISPASS、IISWC、SIGMETRICS，另保留
  PACMI@SOSP、Hot Interconnects 和 workshop/poster 中与流重建直接相关的少量工作。

主会、workshop 和 poster 在账本中使用不同 `venue` 值，图表中也分开统计。

## 3. 检索来源

优先级从高到低为：

1. 会议官方程序与论文页：USENIX technical sessions、MLSys Proceedings；
2. DOI 元数据：Crossref、IEEE/ACM DOI landing metadata；
3. OpenAlex 用于查漏和核对标题/年份；
4. 论文/官方 artifact 与本地已运行仓库，用于核对能力和实验边界；
5. arXiv 只在正式版本尚无稳定会议页或需补 artifact 版本时使用。

本轮实际使用的查询词族包括：

```text
large language model serving
large language model inference measurement / characterization
mixture of experts serving / expert parallel communication
distributed LLM inference communication
GPU collective communication / all-to-all
AI datacenter network / RDMA telemetry
LLM simulation / performance model / workload generation
```

对 SIGCOMM 2024–2026、INFOCOM 2023–2026、OSDI 2024–2025、NSDI 2025–2026、
SOSP 2023–2025、MLSys 2024–2025、SC 2022–2025 的正式程序或 DOI 结果做了标题级复核。

## 4. 纳入与排除规则

满足以下任一条件即可纳入：

- 直接研究数据中心 LLM/MoE inference serving 的测量、调度、缓存、并行、运行时或网络；
- 提供本课题必须对比的 AI collective、All-to-All、RDMA fabric、telemetry 或 congestion control；
- 提供 request/operator/flow/packet 级推理仿真、workload 生成或性能模型；
- 虽以训练为主，但其 flow reconstruction、collective dependency、AI fabric 或 packet simulation
  方法直接限定本课题可声称的创新。

以下内容默认排除：

- 仅改模型精度、解码算法或量化算法，没有系统/硬件/网络测量；
- 纯移动/无线 edge offloading，且与 AIDC 内部网络无可迁移机制；
- 只有营销材料、没有论文页/DOI/artifact 的系统；
- 同一工作的 arXiv 与正式版本重复条目；
- 训练工作若只研究优化器/收敛、与 inference/collective/fabric 无直接关系。

## 5. 字段与证据规则

每条记录至少包含：题目、会议、年份、DOI/官方页、类别、测量对象、粒度、证据类型、优化旋钮、
主要指标、论文报告范围、限制以及与本研究的关系。

`reported_result_or_scope` 只记录论文公开声称的范围，不将其改写为本地实验结果。没有核对到
稳定数值时保留能力描述，不猜测百分比。`limitation` 是相对本课题问题的边界，不等同于论文缺陷。

本地实验继续使用独立口径：

- `measured`：A100/NCCL 实机；
- `profile-derived`：性能库/离线 profile；
- `trace-derived`：框架 trace 直接字段；
- `algorithm-projected`：根据显式 collective 算法展开；
- `analytical`：解析模型；
- `ns3-simulated`：指定网络配置的离散事件结果。

## 6. 当前覆盖结果

账本共 96 项，主会覆盖为：SIGCOMM 31、INFOCOM 9、NSDI 12、OSDI 10、SOSP 5、
MLSys 9、SC 10、ISCA 2；另含 FAST、ISPASS、IISWC、SIGMETRICS、PACMI@SOSP、
Hot Interconnects 各 1 项，以及明确单列的 SC Workshop、SIGCOMM Poster 各 1 项。

覆盖图由 `tools/build_literature_figures.py` 从账本确定性生成；数量表示本课题账本的纳入范围，
不是会议质量或该会议全部论文数量。

## 7. 仍然存在的边界

- 这是面向“AIDC 推理测量、仿真与传输长尾”的系统性工程调研，不是形式化的 PRISMA
  医学式 systematic review；不能声称穷尽所有体系结构、kernel、edge 和算法论文。
- 2026 年条目来自截至快照日已公开的正式程序/DOI；后续 program 变化需重新生成账本。
- 部分生产系统不公开真实 trace、fabric 配置或源代码，因此论文报告值不能替代本地校准。
- 论文通常不会同时给 request semantic、runtime NCCL flow 和 packet/queue truth；这正是本课题
  需要通过多源观测补齐的证据缺口。

## 8. 更新与验证

新增文献后执行：

```bash
python3 tools/build_literature_figures.py
python3 - <<'PY'
import csv
with open('research/literature_ledger.csv', encoding='utf-8', newline='') as f:
    rows = list(csv.DictReader(f))
assert len(rows) == len({row['id'] for row in rows})
assert all(all(value.strip() for value in row.values()) for row in rows)
print(len(rows))
PY
```

随后运行 Sphinx 严格构建和 linkcheck。若会议页、DOI 或分类发生变化，先修账本，再重建图和报告，
不直接手改统计数字。
