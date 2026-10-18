# 当前任务与交接

状态：`completed`。2026-10-18，按用户要求研究 `core12345/MoE_expert_selection_trace` 的结构及热点专家在Scale-Out研究中的用途。已核对公开元数据、论文v5、既有来源清单与解析器；新增有界离线热点统计工具和研究说明。只本地修改/提交，不push。

## 本轮结果

| 项目 | 结果与证据 |
|---|---|
| 数据结构与使用方法 | [研究说明](../reviews/moe_trace_dataset_hotspots_20261018.md)：四模型目录、R1逐请求/入口/层/Top-K结构，Prefill/Decode分离，通信矩阵与专家放置关系 |
| 原论文与差异 | 论文已研究相关性、热点和Prefill引导放置；第VI节实机指标排除All-to-All，本研究应验证跨机网络和端到端等待，不把已有观察当创新 |
| 统计工具 | [操作说明](../../experiments/moe_trace_hotspots/README.md)：复用来源校验，逐层分阶段统计、共选计数和单请求token窗口，不执行网络或下载 |
| 检查 | 4项人工计数/分母/层身份/窗口/来源hash/完整形状/预算检查通过；人工输入CLI成功，真实输入缺失时明确失败且不写伪结果 |
| 来源 | [来源哈希与失败记录](../../experiments/moe_trace_hotspots/sources.json)、[目录摘要](../../experiments/moe_trace_hotspots/metadata-summary.json)；siblings恰好100000项，不声明总体计数完整 |

## 边界

原始HF连接超时，公开镜像元数据可读取；README/raw为401，没有接受新的访问条款。当前clone缺少历史manifest中的真实路由，本轮没有下载、读取新的真实路由，没有真实热点Top榜或训练统计，也没有将数据接入非UB服务。

历史UB路线有真实路由读取能力；当前Scale-Out仍使用合成路由。接入应传递真实请求ID/来源入口并匹配模型结构，不能把R1专家取模用于Qwen3-30B。训练不能由推理数据替代；GPU时序校准、真实到达和物理放置仍需独立依据。

本轮只新增离线研究工具与文档，不改变平台/后端、依赖或大规模运行预算。共选计数未控制边际热度，token窗口不是物理时间，Top-m重合不是预测准确率。

## 前期工作入口

- [batch、实际千卡拓扑和研究范围](../reviews/scaleout_configuration_and_research_scope.md)：千卡为256个EP4副本共享超售三层Clos，历史600秒超时，不声称全局千卡All-to-All通过。
- [运行成本与证据](../../experiments/scaleout_scaling/results/runtime_20261016/report.md)、[离线HTML](../review_artifacts/scaleout_runtime_20261016/report.html)、[规模实验](../../experiments/scaleout_scaling/README.md)。原结果与大文件归档保持原状。
