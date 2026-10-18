# 当前任务与交接

状态：`completed`。补全原运行报告的实际网络布局，并为非UB Scale-Out增加有来源的逐请求专家选择回放。代码和报告本地保存，不push。

## 平台结果

- 可选`route_replay`：按rank/slot指定独立来源文件和Decode入口，核对模型、原层号、E/K、SHA-256、完整原件形状及输入预算；保存可搬移快照，禁止循环补齐和EOF推断EOS。
- 选择生成实际Chakra图，由网络完成回传推进下一轮；允许逐层等容量静态专家重排。按每请求实际context处理KV和完整GQA分支的声明attention成本。
- [公开操作示例](../../examples/scaleout_route_replay/README.md)、[验收与命令记录](../../fixtures/scaleout_route_replay/README.md)：两组4卡人工输入均完成2轮，独立核验每组50条消息、164个节点、16条原始Decode层记录；快照搬移可复核，修改选择即使不改总字节也被拒绝。16张旧默认合成图内容不变。
- 平台检查：回放8项、原RDMA5项、扩展形状6项通过。原根卷启动因8 GiB预留不足被拒绝且worker未启动，记录保留；后改用空间充足的内存盘，所有原预算保持不变。

## 报告和研究结果

- [原HTML报告](../review_artifacts/scaleout_runtime_20261016/report.html)新增实际ToR/Agg/Core连线、同机/跨Pod示例、源/目的GPU、EP组及容量比。交换机全绘制，GPU按Pod汇总；计算路径不是历史ECMP包路径。
- 8个历史用例的物理输入与原SHA-256一致，去重为5份布局。嵌入测量与原JSON相同，6次完成和2次千卡超时保持原值；千卡仍是256个EP4副本，不声称EP1024完成。
- [展示检查](../review_artifacts/scaleout_runtime_20261016/report.validation.json)：Node DOM桩覆盖全部布局、物理路径、新控件及原筛选/导出，重复生成HTML字节一致。本机无浏览器，未做截图或视觉验收。复现见[实验说明](../../experiments/scaleout_scaling/README.md)。
- [数据集研究](../reviews/moe_trace_dataset_hotspots_20261018.md)保留四模型目录、R1原始结构、热点定义、原论文基线及网络研究方向；[离线工具](../../experiments/moe_trace_hotspots/README.md)4项检查通过，公开4请求人工输入CLI成功。来源元数据/论文哈希仍见该工具目录。

## 使用边界

原始受限路由未取得，无新增真实热点排名、真实源网络复现或训练结论。人工样例证明接口，不证明优化收益。来源选择可以真实，部署、到达、激活dtype和计算时序仍需独立依据；不把R1专家取模用于Qwen3-30B。

`service/1`执行1—4个连续MoE层，KV为通用片段声明；`service/2`回放要求支持的全MoE GQA结构及全层覆盖，不支持完整R1的Dense层/MLA。服务专家数上限仍为256，离线分析至1024。没有专家复制、动态迁移或DeepEP目的地去重；不下载大数据、不改依赖、不运行千卡、不增加预算。

## 前期入口

- [batch、千卡拓扑和研究范围](../reviews/scaleout_configuration_and_research_scope.md)。
- [运行成本原始证据](../../experiments/scaleout_scaling/results/runtime_20261016/report.md)；原历史运行与大文件归档保持原状。
