# M2.1 四 rank 单层功能输入

本目录实现 v0.2 第 12.1/12.2 节的确定性样例。它是合成数据和参考通信模板，没有真实模型、路由轨迹或硬件校准依据。

`layer.json` 明确给出 A～D 的一个解码 Token、`token_index=9`、`layer_id=7`、Top-K=2、隐藏维度 4096，以及八个专家的放置。每个元素 2 字节，应用元数据为零，因此每份远端向量为 8192 字节。使用 `direct_rank_dedup_v1`、一个发送槽、单块传输，优先级统一为 7。

Dispatch 字节矩阵为：

| 源/目的 rank | 0 | 1 | 2 | 3 |
|---|---:|---:|---:|---:|
| 0 | 0 | 8192 | 8192 | 0 |
| 1 | 8192 | 0 | 8192 | 0 |
| 2 | 0 | 0 | 0 | 8192 |
| 3 | 8192 | 8192 | 0 | 0 |

Combine 独立按结果向量生成，在本输入的相同精度和合并规则下为上述转置。每阶段七条传输，两阶段十四条、114688 字节。每条传输有不同 transfer_id、UB task_id、comm_tag，以及一对 SEND/RECV。expert_inputs 分别记录 2、2、3、1 份；rank 2 包含 C 的本地专家 4 输入，这份输入和结果不生成 UB 任务。

固定计算 profile 的单位为 ns：

| 节点 | 每 rank 的耗时 | 等待条件 |
|---|---:|---|
| attention-route-pack | 5000 | 图激活 |
| expert-compute | 每份专家输入 4000，即 8000/8000/12000/4000 | 本地准备和本地全部 Dispatch SEND/RECV 完成 |
| expert-local-merge | 1000 | 本地专家计算完成 |
| combine-unpack-merge | 1000 | 本地结果处理和本地全部 Combine SEND/RECV 完成 |
| next-layer | 1000 | 本地 Combine 处理完成 |

这些耗时是功能输入的显式假设。attention-route-pack 包含本样例的注意力、路由与打包；expert-local-merge 表示专家结果的本地处理；combine-unpack-merge 表示接收结果解包与合并。next-layer 仅为验收后继依赖的 COMP，不是一份完整的下一层模型。所有依赖均在本 rank 内，跨 rank 通过配对收发等待；没有加入全组屏障节点。

网络是四个 DEVICE 经一个四端口 SWITCH 相连，rank 与 UB 端点为 0～3，交换机为 4，链路为 400 Gbps、20 ns。协议属性继承已验证的 UB 两端点参考样例。为在运行前登记被动来源通知，本测试预建六对优先级 7 的双向 RTP 连接，每个端点三个 TP，并固定保留连接。此处只验证功能，不据此评价网络性能。

`observation.json` 选择交换机 4 的端口 2 egress，观察 `[0,100000)` ns，以 1000 ns 分窗。该端口接收 Dispatch 0→2、1→2 以及 Combine 3→2，业务字节应为 24576。来源开启、关闭和不挂观测分别运行时，实际执行应一致。

可运行命令见 [联合程序说明](../../backend/README.md)。生成器在输出目录生成本次 protobuf 编码模块、四份 layer.<rank>.et、plan.json 和生成哈希；不写依赖目录，不读取旧的未跟踪生成模块。本目录保持单块、单槽输入。M2.3 已加入 [去重、分块和两槽变体](../m2_variants/README.md)，零应用元数据和每 rank 正专家计算量的范围仍保留；零计算标记与连续图尚未验收。
