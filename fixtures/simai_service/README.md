# SimAI与原生网络功能验收

公开用户流程见[小例](../../examples/simai_service/README.md)。2026-10-28在新目录`runs/simai_native_platform_20261028/`实际执行正常和数据/ACK丢失各一次；64物理GPU、两人工源请求、EP64、一个MoE层/两步Decode，同一个原生ns-3时钟。

[独立摘要](verification.json)核对64条消息、917,504有效字节、640个ASTRA节点。服务完成47.603→167.013µs；故障引发3次超时重传，全部请求结束且KV块归零。两份完整包捕获的公开arrival审计通过。`tests/integration/check_simai_service.py`只审核保存结果，不重跑网络，并拒绝完成反馈、原专家选择、闲rank计算量和有效字节的四种篡改。

另有4项稀疏输入/搬迁/独立BFS等价单元检查、8项旧路由回归和7项旧RDMA回归通过；上游CPU生命周期及165份来源完整性复核通过。不是实际用户路由研究、硬件校准、现代DeepEP、完整R1或320卡完成证据。
