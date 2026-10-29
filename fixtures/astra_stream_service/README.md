# ASTRA主服务持久传输功能验收

公开输入与命令见[示例](../../examples/astra_stream_service/README.md)。该增量保持真实前端、ASTRA依赖和资源执行，仅改变消息传输；不是保存DAG重放，不宣称现代DeepEP完整语义、生产RoCE精度、千卡任务或算法收益。

## 已实际执行的条件

| 条件 | 前端与规模 | 结果 |
|---|---|---|
| 默认逐消息兼容 | LLMServingSim，4端点 | 50消息/164节点；旧native字段除运行库路径外全部相同 |
| 持久SR正常 | LLMServingSim，4端点 | 50消息、25,600B，业务67.668µs |
| 中间数据包丢一次 | 同上 | 成功，业务79.861µs；不能把该功能差异当新算法收益 |
| 消息末尾ACK丢一次 | 同上 | 成功，业务67.668µs；后续累计ACK覆盖，0超时，**不计作连接尾部恢复覆盖** |
| 双EP2组 | LLMServingSim，4端点 | 共享一个网络，26消息/116节点，业务25.944µs；本例不证明共享瓶颈 |
| 连接最后ACK丢失，无后续工作 | LLMServingSim，4端点/单层单步 | 1次真实超时，256B重传，最后接收13.913µs、源确认112.030µs |
| 连接最后数据包丢失，无后续工作 | 同上 | 1次真实超时，256B重传，最后接收106.006µs、源确认112.030µs |
| 目的端持续黑洞 | LLMServingSim，4端点 | 实际首次COMMIT后重试耗尽，1201.030µs停止；业务完成/排空时间均为空。全网其他目的端共接受5,120B，不能宣称全网0到达 |
| 稀疏SR | 实际SimAI/Vidur，64端点 | 64消息/640节点，业务47.603µs |
| 同参考CC的持久GBN正常 | LLMServingSim，4端点 | 50消息、25,600B，业务67.668µs；仅正常可用性，不是完整GBN/SR性能对照 |

输入均是有源码/清单校验的人工功能样本；SimAI例采用R1形状，不是新真实请求测量。旧路径和最初四个SR成功条件只运行一次并保留原件，本轮未重跑；本轮新增四个修复/覆盖条件与一个持久GBN组合。持久GBN/SR尚无同真实输入的故障性能对照。

## 控制通道缺陷与修复依据

最初黑洞用例在首次DECIDE、now=0时被上游异步stdout日志污染，Python报告JSON Extra data；history/transcript为空，无native结果，不能视作恢复失败。原失败保留在`runs/astra_stream_service_20261029/acceptance/blackhole/`。新用例在独立目录执行，没有覆盖原件。

`ControlChannel`在初始化任何logger之前复制原stdout管道，再把普通stdout转stderr；只有控制帧写私有描述符，后续新建logger也无法污染该管道。支持部分write/EINTR，严格拒绝非法帧。CPU实管道检查覆盖128条各含8192B载荷的帧、384条并发iostream/stdio/direct-fd诊断和3个拒绝，不依赖单次写入小于PIPE_BUF。两Python前端不截取损坏JSON前缀继续运行。

## 核验、构建和复现

公开服务检查从声明/原始选择重新生成图，对照实际ASTRA issue/callback，逐包独立核对接收前缀、源确认、消息区间、物理NIC及唯一字节守恒。失败小例额外检查原生结果存在、首次任务确实提交、恢复耗尽原因及没有成功终态。新的`rdma-arrivals`持久摘要使用`/2`，保存旧`/1`导出且在新目录重导，不重跑网络。

两个实际验收驱动：[首批](../../tests/integration/check_astra_stream_service.py)和[控制修复后的补充](../../tests/integration/check_astra_stream_completion.py)。前者的ACK丢失不再被误作尾部超时证明；后者只运行四项新条件。首次冷环境仍须按用户指南构建和准备固定runtime；不要求任何旧runs作为输入。

编译前保存旧二进制/收据，服务增量构建一次成功；8个固定ns-3库哈希完全不变，零依赖修改/下载/安装。C++控制核心独立编译和检查通过；660项Python CPU检查通过。原生日志中的既有编译警告保留，不将CPU测试称为全部C++测试通过。

机器摘要见[verification.json](verification.json)。原件根：`runs/astra_stream_service_20261029`与`runs/service_control_fix_20261029`；前者旧build收据不再代表本地现有二进制，当前对应收据为后者`backend.json`。输出完整度、资源预算及源代码哈希均随摘要记录。这里是平台验收；研究输入、对照方法与收益必须另建实验、单独提交。

未覆盖/未提供：自然MMU全丢弃观测、服务失败隔离、机内crossbar与SR组合、动态rail/连接、完整R1/MLA硬件计时、真实共享瓶颈优化和千卡长期MoE执行。现有资源与完成语义不因这些限制而放宽。
