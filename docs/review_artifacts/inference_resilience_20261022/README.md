# 本阶段审阅与复现

- [整体交付](architecture.html)：目标、服务、约束、平台关系、实现维度与后续候选。
- [24组结果](report.html)与[原始指标摘要](summary.json)：包含负结果、确认反馈与资源链、重复ACK排空。
- [条件证明](formal.html)：带假设的安全性、失败界、准入与历史性质。
- [完成核对](completion_audit.md)、[重新诊断](verification.json)、[证据归档](archive.json)。

用户入口见[完整配置与命令](../../../examples/scaleout_rdma_resilience/README.md)，独立研究见[研究说明](../../../experiments/inference_resilience/README.md)。`build_review.py`从本轮报告与现行条件证明生成两个HTML；研究的三个输出由`analyze.py`从原始研究目录重建。浏览器视觉验收未进行。

本轮平台从`a329e19`推进至`52e1afb`；研究与交付文档另作本地提交，没有推送。旧轮`inference_fragility_20261022`的8192B/FIFO条件与本轮16384B/目的端轮转不同，不能直接比较绝对时延或覆盖历史证据。早期全局LRU产物只作修正历史，不是最终每源独立缓存证据。
