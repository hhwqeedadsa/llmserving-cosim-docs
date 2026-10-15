# MoE训练热点：原始论文、训练框架与网络建模的对应关系

研究对象是**专家负载经物理部署和通信实现后，怎样形成跨服务器网络负载与长尾**。本报告补充[千卡方案](scaleout_scaling_hotspots_20261015.md)的训练依据：实际读取 DeepSeek-V3 v1 原始论文和固定版本 Megatron 源码，核查现有 ASTRA/SimAI 的集合通信输入，并用5个小型合成输入验证字节关系。

这里没有训练模型，没有读取新的真实路由JSON，没有测得训练热专家比例或网络延迟。用户提供原始路径后再补真实统计；本轮HF元数据访问仍超时，不能声称已核查整个远程数据集。成功/失败来源与SHA-256见[来源记录](../../experiments/scaleout_scaling/training-sources.json)。

## 1. 比“训练存在热专家”更准确的判断

**训练可以在全局batch上保持平衡，同时在某些层、序列、微批和网络位置上不平衡。** “热”必须给出统计对象和时间窗口，不能从全程累积频率直接推导某次All-to-All的网络热点。

DeepSeek-V3 [§2.1.2](https://arxiv.org/html/2412.19437v1#S2.SS1.SSS2)明确：训练按整批专家负载调整每专家bias，过载时降低、欠载时提高；bias影响Top-K选择，最终用于FFN输出的gating值仍来自原始分数。还保留很小的序列级辅助平衡损失以避免单序列极端失衡。其含义是：

- 不应默认训练分布是一条贯穿全部step不变的Zipf曲线；路由有反馈机制，checkpoint、step与更新策略会改变分布。
- 全局平衡不强制每个源rank、任务域或短窗口都均匀；大batch汇聚后的平衡不能替代逐层、逐微批检查。
- 网络仿真回放选择结果可以研究固定输入下的网络机制；如果要修改路由bias或balance loss，只有专家ID不足以预测新选择，需模型/logits和训练验证。

论文 [§4.5.3](https://arxiv.org/html/2412.19437v1#S4.SS5.SSS3)及附录C展示了不同领域的专家专门化，并讨论单序列/小batch不平衡。**图9与图11的负载来自训练后16B模型在Pile测试集上的分析，不是2048卡训练过程的逐step路由trace，也不是R1-AWQ/MMLU测量。** 可以据此提出领域相关的热点假设，不能挪用其图来填本机训练热点指标。

应分别问：整步是否平衡？单microbatch是否平衡？同一层不同DP源是否相关？相同热点是否连续存在？相同负载在rank和NIC上是否合并？上述问题可能得到不同答案。

### 1.1 均匀随机也不等于每专家计数相等

在token之间独立、每token从E个专家均匀无放回选K个的基线下，某个专家在T个token中的计数服从边际二项分布：均值 `μ=T×K/E`，方差 `T×(K/E)×(1−K/E)`，单专家跨重复批次的变异系数 `sqrt((1−K/E)/μ)`。同token内不同专家的选择并不独立，不能据此直接给最大负载的置信区间。

例如E256、K8、一个EP组总共64个token，某专家均值2、标准差约1.392、变异系数约69.6%，未被选专家数的期望约33.6；256个token时均值8、标准差约2.784、变异系数约34.8%。这是分析公式，**不是R1或训练实测**。小batch里出现未使用专家或某专家高于平均，单凭这一点不足以证明学习出的热点。

因此应把“等量collective”“有限样本uniform随机路由”“真实相关路由”分为三组。前者删除了采样方差，第二种保留方差但缺少领域/时间/源位置相关，第三种还可能受到训练平衡反馈。区分它们比统一称为random更有用。

## 2. 真实训练路由包含网络约束

DeepSeek-V3 [§2.1.2、§3.2.2](https://arxiv.org/html/2412.19437v1#S3.SS2.SSS2)和§4.2给出具体结构：训练集群2048张H800、每服务器8卡；每层专家部署在64卡、8个节点上，每token最多选择4个目标节点。token先经IB发到目标节点内相同索引的GPU，再经NVLink转发给专家；Combine包含相反方向的转发与累加。

论文§3.2实际同时采用PP16、EP64和ZeRO-1 DP，而不是一个2048成员的EP组。§4.2的batch调度从3072增至15360，不能直接把这个数字填入“每GPU序列数”；需要先取得microbatch、累积、流水映射和DP组定义。当前核算命令限定TP=PP=1，是通信规模参考，不能直接复现这份训练配置；完整训练仿真必须增加流水及ZeRO同步依赖，而不是只调整EP参数。

因此有三种不同的量：Top-K专家数、目标GPU数、跨机目标节点数。不能写 `IB字节=token×K×H×dtype` 而忽略节点级聚合；也不能把“每token最多4节点”理解为全网最多4个参与节点。

论文给出的IB/NVLink带宽和SM分配属于其具体实现与研究条件，不是所有H800、A100或RoCE部署的统一参数。本研究的新后端采用RDMA/RoCE基线时，可以借鉴路由和转发结构，但**不能把IB数据直接作为已校准的RoCE拥塞结果**。需要显式配置GPU-NIC亲和、rail、PCIe、节点内转发、链路带宽与交换机结构。

本轮容量命令的均匀、无节点限制Top-K公式仍只是控制条件；它不模拟这个真实路由机制。如果恢复的trace来源声明包含节点限制，必须保留采集时的专家分组和放置关系，或者明确映射变化后已不再满足原限制。

## 3. 固定版本Megatron源码证实了哪些行为

本轮读取 [NVIDIA/Megatron-LM `915b1d2d427e526dabc4a0b460562c257376025c`](https://github.com/NVIDIA/Megatron-LM/tree/915b1d2d427e526dabc4a0b460562c257376025c)，只阅读4份源码，不安装、不运行该训练框架。下列行号对应该固定版本，不能泛化为所有历史版本或用户训练的实际配置。

| 机制 | 源码依据 | 对网络仿真的影响 |
|---|---|---|
| 平衡统计范围 | `transformer_config.py:864`列出microbatch、sequence、global batch等loss选项 | 需要记录实际选项及统计组，不能把“平衡”视为每次通信都等量 |
| group-limited路由 | `transformer_config.py:901`、`moe_utils.py:692` | 分组可对应GPU或节点；先选组，再在组内选专家，独立uniform Top-K不保持此约束 |
| 动态expert bias | `transformer_config.py:940` | bias按全局batch负载更新，需要step与checkpoint背景才能解释时间变化 |
| 强制随机路由 | `transformer_config.py:957`默认False，`router.py:1262`调用random logits | 明确是benchmark选项，不应说真实训练默认随机，也不能把该设置当真实路由 |
| 容量裁剪 | `router.py:1103`、`moe_utils.py:1066` | 路由选择之后再裁剪，容量按调用范围的token与Top-K计算；accepted与selected不能混用 |
| pad-to-capacity | `token_dispatcher.py:523` | 可以给各专家分配同样容量的传输/执行槽，均匀的槽数不证明真实选择均匀；空槽开销要计入 |
| 动态split | `token_dispatcher.py:565` | input/output splits来自实际routing_map各专家计数及组间交换，是可变长度All-to-All的直接依据 |
| tokens与probs分别通信 | `token_dispatcher.py:688` | 该AlltoAll分支分别发送激活和路由权重；只模拟激活向量会遗漏额外通信及其依赖 |

固定源码入口：[配置](https://github.com/NVIDIA/Megatron-LM/blob/915b1d2d427e526dabc4a0b460562c257376025c/megatron/core/transformer/transformer_config.py#L864)、[路由](https://github.com/NVIDIA/Megatron-LM/blob/915b1d2d427e526dabc4a0b460562c257376025c/megatron/core/transformer/moe/router.py#L1103)、[工具](https://github.com/NVIDIA/Megatron-LM/blob/915b1d2d427e526dabc4a0b460562c257376025c/megatron/core/transformer/moe/moe_utils.py#L1066)、[dispatcher](https://github.com/NVIDIA/Megatron-LM/blob/915b1d2d427e526dabc4a0b460562c257376025c/megatron/core/transformer/moe/token_dispatcher.py#L523)。源码还提供其他dispatcher，这张表只说明已读分支，不能宣称所有通信实现都逐专家复制或都发送相同probs布局。

容量公式在此版本的相应调用中为 `ceil(local_tokens×TopK/E×capacity_factor)`，其中local_tokens是当前routing_map的行数。是否已经包含TP/CP等分发，须结合调用路径判断，不能用全局优化器batch直接替换。默认capacity_factor=None，不drop；pad-to-capacity默认False。论文DeepSeek-V3声明其训练不drop，也不意味着所有MoE训练都采用这个策略。

特别注意：只保存未裁剪的Top-K ID，不能无歧义恢复按probs裁剪后的accepted集合。若研究drop/pad，需采集最终routing_map、有效槽位与实际split；对比分别报告有效token、空槽和丢弃量。

## 4. “仿真一般random”要落实到具体输入路径

本轮查到的具体不足是**集合通信只接收总字节，展开为等长对等通信时会丢失MoE矩阵**，这与随机路由是两回事。

- 本机固定SimAI `a57d765c...` 的 [`MockNcclGroup.cc:475`](https://github.com/aliyun/SimAI/blob/a57d765c5478f319c5f5ab6fdc53f9cc7f461743/SimCCL/src/mock/v2.30/MockNcclGroup.cc#L475) 中，`genAlltoAllFlowModels`计算 `chunksize=data_size/nranks`，遍历每个不同src/dst创建同样大小的flow。该分支没有逐专家路由矩阵参数；这是等分All-to-All展开，不能描述任意真实热点。这里是源码核查，没有运行此SimAI分支。
- 本仓锁定ASTRA [`Ring.cc:83`](https://github.com/baoy21/astra-sim/blob/0210e24bf76f49b4111a73d1e419f6fee4f3f0b6/astra-sim/system/collective/Ring.cc#L83) 中对应All-to-All分支也以 `data_size/nodes_in_ring`设置msg_size。结论限定该集合算法路径，不代表ASTRA不能执行其他有依赖的点对点任务。
- 师兄仓库已经按真实token→expert与显式放置生成点对点SEND/RECV，因此应**保留该优势接入非UB后端**，而不是为了复用某个collective接口又退回一个总字节数。

建议同时保留三类输入：等量All-to-All用于网络功能和容量基准；带种子的合成偏斜/节点限制模型用于敏感性扫描；有来源的真实路由用于结论。任何一种都不能替代另外两种。

## 5. 五个可重现反例：频率不是网络负载

使用8个专家、4个rank、每服务器2rank、每token选2专家、向量8B。纯整数核算Dispatch，**不是训练数据、不是ns-3运行、不是时延预测**。配置固定了所有token与放置，没有随机采样。

第一组的8个token全部来自rank0，均选择专家0/1，其他专家未选。仅移动专家位置：

| 条件 | 专家0/1位置 | 按目标rank去重的跨机payload | 节点级合并后的跨机payload | 专家计算输入最大值 |
|---|---|---:|---:|---:|
| hot_local | 都在rank0 | 0 B | 0 B | 16 |
| hot_remote_colocated | 都在rank2 | 64 B | 64 B | 16 |
| hot_remote_split | 分别rank2/3，同一远端服务器 | 128 B | 64 B | 8 |

三种条件的专家选择频率完全相同。分散专家使每rank专家输入峰值减半，但直接按rank发送的跨机字节增一倍；节点级合并能保持跨机字节，却增加/改变节点内转发与归并。不能只按专家负载均匀性选择放置。

第二组固定放置与最终rank字节矩阵，4个token选专家0/1、4个选6/7：连续排列AAAABBBB，或交替ABABABAB。以每4token为声明的统计窗口，目标rank的窗口最大payload为32B与16B，而两种条件总计都向rank2/3各发送32B。**这里的窗口是token分组，不是纳秒或真实microbatch时间**；它只证明长程直方图会丢失分组信息，不证明网络排队下降50%。

配置、计算程序和手工计数一致性结果见[实验说明](../../experiments/scaleout_scaling/README.md#专家热点的确定性反例)。真实实验必须进一步给出到达依赖、NIC连接、路由和服务能力，才能推导队列与业务影响。

## 6. 可落地的训练热点研究方案

1. **先固定回放对象。** 声明模型版本、训练阶段/checkpoint、层、E/K、微批及累积、TP/CP/PP/EP和expert-DP、balance策略、drop/pad与通信库版本。推理R1-AWQ/MMLU只作为推理来源，不补造训练step。
2. **采集两层路由。** 选中专家用于解释模型偏好；accepted/padded routing_map及split用于计算实际通信。源rank/token/微批/层和物理放置必须保留。需要改路由算法时另采logits或重跑模型，不能凭专家ID生成新的“真实路由”。
3. **恢复依赖。** 前向和反向激活通信、梯度bucket、计算与准备节点均进DAG；metadata/probs、转发/归并和缓冲限制按实际实现展开。训练不复用推理FCFS组批作为训练调度。
4. **分析三种平衡尺度。** step/全局batch、microbatch/源rank、NIC/端口窗口分别统计；同时检查领域、层间与时间相关性。全程平均、每层峰均比和真实业务尾部分开报告。
5. **做保持条件的对照。** 固定真实选择改物理放置；固定总量打乱微批分组；保留节点限制的合成模型；等量collective控制。改变路由与改变物理放置不能混成同一“调度优化”。
6. **最后验证网络价值。** 判断热点是否造成网络服务不足、incast或反馈传播，是否在关键依赖暴露。比较策略时报告真实吞吐/step或请求完成、尾部、跨机字节、转发/SM/显存与迁移代价。没有时序数据时只输出负载分析，不填网络加速比。

近期优先研究问题可收敛为：**在全局batch已平衡的训练中，microbatch/源位置相关性和节点级转发是否仍改变NIC拥塞及策略排序？** 若原始数据表明这种影响很小，应接受负结果；若影响显著，再设计放置或网络策略并计入其成本。
