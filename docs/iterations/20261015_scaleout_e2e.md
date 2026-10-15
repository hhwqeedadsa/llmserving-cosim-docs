# 2026-10-15 小规模原生RDMA联合实验

目标是基础Scale-Out实验与LLMServingSim到非UB ns-3的实际多轮流程，非千卡性能结论。平台本地提交 `3897d82`；研究脚本、图表和证据另作本地提交，不push。

新增独立RDMA构建/探针/服务/核验公共CLI。每rank持久ASTRA Sys，共享唯一Fabric与Simulator；接收端连续字节齐备与发送完整ACK分开回调，实际完成推进下一轮Scheduler。拓扑直接装载参数化Clos/Agg/rail/plane输入，Core使用普通交换机。串行MPI兼容修改只产生构建目录overlay，固定依赖源码/gitlink保持不变。

[研究结果](../../experiments/scaleout_e2e/conclusions.md)涵盖30次主对照、rail可达性、交错EP4共享出口、协议微实验、零专家成本诊断与模型变形。[离线网页](../review_artifacts/scaleout_basic_20261015/report.html)提供7组实际数据图。[版本记录](../../experiments/scaleout_e2e/evidence/versions.json)解释实施工作树、后续零时长修复与复现核验的对应关系。

关键发现：降上联带宽使接收和专家消费者后移；固定总带宽的双plane未在该小负载获益；专家热点不等同最大网络queue；EP4收益与放置/副本变化混杂。已修正ECN字节/KB单位和headroom汇总顺序，修正前矩阵归档。尾包丢失无NACK触发、不能自动恢复，保留预期失败，不弱化完成断言。

31次主/rail服务的42712节点独立核验，基线原生结果和36文件逐字节复现；交错EP4另972节点通过。28项相关单元检查、原有导出兼容和13项依赖检查通过。完整证据20.2 MB持久压缩备份，3019个成员hash核验；运行预算、当前状态统一见[任务页](../tasks/current.md)。功能验证不等于A100校准、真实训练热点、完整HPN或千卡逐包能力。
