# 模型依据与适用范围

本页说明仿真中的计算、通信和容量分别依据什么、采用哪些近似，以及现有验证能支持什么结论。运行方法见[用户指南](user_guide.md)，模块与执行顺序见[系统架构](architecture.md)，完整参考场景见 [CM384 / DeepSeek-R1](scenarios/cm384_r1.md)。

当前平台可以执行完整 Decode 计算与通信依赖图，并用 ns-3-UB 的实际完成事件推进后续任务。计算节点使用声明的耗时，不执行张量数值运算；通信使用独立实现的行为模型，不加载 CANN 或 DeepEP 内核。真实路由来源、参考模型的功能正确性和目标硬件校准是三项独立依据。

操作阶段与队列等待统计来自仿真中的真实节点、回调和队列状态变化，分别记录 VOQ 与 FIFO，不从流量形状推断等待量。小型原生执行已经检查统计启停的执行一致性和独立积分，证据见[操作与队列输入](../fixtures/m14_operation_queue/README.md)。这些信息在目标设备上的采集接口、精度及硬件开销尚未验证，仿真常驻内存与输出成本不能替代硬件测量。

聚合保存直接累计实际物理发包、队列状态与应用消息来源；业务数据、附随元数据、计数发布、完成标志、LOAD 就绪通知分别登记，协议确认不归为应用状态。纯聚合仅允许对齐地合并窗口；任意边界或时钟变化需要覆盖所选区间的详细事件及初始队列状态。小例已检查与详细记录等价、选择性保存、预算拒绝及旧详细数据兼容，见[聚合保存证据](../fixtures/m15_aggregate_capture/README.md)。应用消息分类和仿真内部来源只用于评价，不能作为已确认的硬件观测能力。

## 计算模型

### 完整 Decode 图

Decode 指逐步生成新词元的解码阶段；MLA（多头潜在注意力）使用压缩的注意力缓存。当前完整模型入口 `moe-ub-configured-service/4` 使用 MLA `model/2`，张量并行 TP 和流水线并行 PP 均为 1，初态为 KV 已就绪。每个 rank 是参与分布式计算的逻辑成员，本平台不要求一个 rank 对应一个宿主进程。每个 rank 对本地选中的请求执行 embedding、各 Transformer 块、final norm、lm_head 和 sampling；没有本地请求的 rank 仍处理收到的专家工作与必要状态消息。

每块包含 attention norm、MLA 投影与缓存处理、上下文注意力、输出投影、残差、FFN norm，以及 dense FFN 或 MoE 分支。MoE 分支同时表达 routed experts 和本地 shared expert，二者结果都就绪后才能合并并进入下一块。`sampling` 表示末端阶段耗时，不生成文本或推断 EOS。阶段定义与生成实现见 [full_model.py](../python/moe_ub_cosim/full_model.py)。

完整模型成本采用 `moe-ub-full-model-cost/1`，`basis=declared_reference`。成本声明必须匹配模型结构、层型、维度与精度；除专家放置外，结构变化后需要重新声明成本，不能静默沿用。

| 计算对象 | 持续时间 |
|---|---|
| 按 Token 计费的首尾及逐块阶段 | 本地批次 Token 数 × 对应 `per_token_ns` |
| `mla_attention` | 本地各 Token 的 `(token_index + 1)` 之和 × `attention_per_context_token_ns` |
| routed expert | 该 rank 实际收到的 Token×专家贡献数 × `routed_expert_per_input_ns` |
| shared expert | 本地 Token 数 × 本层 shared expert 数 × 单专家参考成本 |
| CANN 局部处理 | 按声明的计数检查、重排、结果打包、本地交付和归约成本计费 |

正工作阶段要求正成本。没有 shared expert 的层，其 shared expert 和 shared/routed 合并成本为零，并省去相应节点。完整模型中 CANN 子层的 `attention_route_pack` 仅表示 Dispatch 量化打包，不能重复计入已创建的 attention/router；`expert_local_merge=next_layer=0`，Combine 检查与归约也只计一次。

可选 `endpoint_prepare` 表达已有成本尚未包含的端侧额外工作：Dispatch 按该 rank 本地 token 数、Combine 打包按该 rank 实际专家输入数计费，分别只加一次到已有准备/结果打包 COMP 节点。零工作量不产生额外节点；其他计算和本地交付成本不变。这是显式参考耗时，并非新增硬件测量；字段和边界见[后端说明](../backend/README.md)。

旧模板全部 COMP 节点竞争每 rank 的一个计算槽。A3 局部资源模板将声明的通信核分别建模，普通模型计算仍竞争一个 `gpu_compute` 资源；共享专家可与独立通信核工作重叠。完成顺序由依赖与资源共同决定。层间保持 rank 本地依赖，不增加全 rank 逐层屏障。整轮所有成员完成后，调度器才推进本轮请求。图执行细节见[系统架构](architecture.md)，阶段与结构检查见[完整模型测试输入](../fixtures/m10_full_model/README.md)。

### 测量表的适用范围

旧层段路径支持按实际形状查找 `operator_profile`。已保存的测量依据来自 NVIDIA A100 80GB PCIe、PyTorch 2.8.0+cu126 / CUDA 12.6：22 项 H=F=256、FP16 的串行 gated FFN 与 GQA SDPA MATH 形状。原始重复样本、测量方法、脚本与输入摘要的定位见 [算子与路由证据](../fixtures/m9_evidence/README.md)。

这些数值包括所选 eager 实现的发射间隙，覆盖的是相应算子形状。未覆盖形状会被拒绝，不按峰值算力折算或线性外推。当前 CANN 与完整 MLA 模型路径拒绝不匹配的 `operator_profile`；A100 小形状表不能用于 R1 的 H=7168、MLA、INT8 或 Ascend NPU。CM384/R1 参考场景仍使用明确声明的合成成本。

## 通信行为模型

### 通信模板的区别

| 模板 | 贡献与返回规则 | 依据和用途 |
|---|---|---|
| `direct_rank_dedup_v1` | 同一 Token 发往同一目的 rank 的多个专家时只发送一份输入；专家计算分别保留，按声明的本地加权合并约定返回一份向量 | 早期参考模型。已核对 DeepEP v1.2.1 的单机 normal 路径，但没有复现其完整元数据、通知、通道或 NVLink 时序。 |
| `cann_a3_v850_fullmesh_v1` | 每 Token×专家分别发送和返回，同一目的 rank 不去重；Token 所属 rank 收齐后归约 | CM384/R1 WRITE 基线采用的独立 CANN 行为模型，以下说明适用于此模板。 |
| `cann_a3_v850_native_v1` | 保留上一行的数据布局和状态消息；向量采用可选 STORE/LOAD，LOAD 前增加真实供数通知 | 本平台定义的原生操作映射，不能把新增握手或 worker 调度称为 CANN 真机行为。 |
| `cann_a3_v850_store_resources_v1` | STORE/STORE；按源码分核、有限局部缓冲及逐 Token 条件执行，分开局部复用和协议回收 | v0.5 平台参考模板；核数量和复制栅栏映射为显式假设，未硬件校准。 |

DeepEP 的固定源码版本为 `9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`。其四 rank BF16 调用配置、八项源码对照和已知差异保存在[核对证据](../fixtures/m5_model_audit/README.md)与[原始完整核对记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/model_validation.md)。这部分是历史模型依据，不能代替当前 CANN 规则。

### CANN 来源与调用范围

固定来源为 [CANN ops-transformer v8.5.0](https://gitcode.com/cann/ops-transformer/tree/6ead121aded45355043b502756b6592fd7c30b14)，提交 `6ead121aded45355043b502756b6592fd7c30b14`。八个来源文件的路径、SHA-256、大小、调用配置与许可证摘要保存在 [source-evidence.json](../fixtures/m10_cann/source-evidence.json)。来源使用 CANN Open Software License Agreement Version 2.0；本平台没有链接或执行这些内核。

选择 A3 的 MoeDistributeDispatchV2 / MoeDistributeCombineV2，API 配置为 `fullmesh_v1`，使用非 `fullmesh_v2` 分支、TP=1、`expertShardType=0`。Dispatch 使用动态 INT8（`quant_mode=2`，无 `smooth_scales`），Combine 使用非量化 BF16（`quant_mode=0`）。不包括专用 shared expert rank、弹性或特殊专家、二维专家 mask、其他架构和其他量化模式。各 rank 使用相同物理输入槽数，通过前缀 active mask 表示活跃请求；零活跃请求不表示原生算子允许 BS=0。

下表的路径相对固定提交的 `mc2/`，D 表示 `moe_distribute_dispatch_v2`，C 表示 `moe_distribute_combine_v2`。

| 行为 | 固定来源定位 | 当前表达 |
|---|---|---|
| 内核选择 | D/C 的 `op_kernel/*_v2.cpp` | 只采用上述 A3、量化与 TP 分支。 |
| Dispatch 复制与地址 | D 的 `op_kernel/moe_distribute_dispatch_v2.h`，379–386、545–565、653–714 行 | 按 Token×Top-K 贡献发送，保留连续均衡分核顺序，同目的 rank 不去重。 |
| 计数和等待 | 同文件 `SetStatus`、`WaitDispatch`、`Process` | 每源 rank 向每专家发布 32 B 状态，包括零贡献。目标本地专家等待全部源计数与数据后重排；`SyncAll` 表示本地核同步。 |
| 返回顺序 | 同文件 `LocalWindowCopy`；C 的 `BuffInit`、`ExpertAlltoAllDispatchCopyAdd` | 按本地专家、源 EP 序号、源 Token 排列；Combine 均衡分核并在核内按 EP 序号旋转，每贡献返回一份向量和 32 B 完成标志。 |
| Token 归约 | C 的 `LocalWindowCopy`、`Process` | Token 所属 rank 收齐 Top-K 结果后加权归约；远端专家 rank 不先合并同 Token 的多个结果。 |
| 缓冲区 | D 的 `op_host/op_tiling/moe_distribute_dispatch_v2_tiling.cpp` 中 `CheckWinSize`；两内核 `BuffInit` | 保留数据、状态和 staging 空间，当前图完成前不复用。 |

上述规则由 [cann.py](../python/moe_ub_cosim/cann.py) 生成，并由 [cann_plan.cc](../backend/cann_plan.cc) 独立核对。具体测试与已知近似见 [CANN 测试输入](../fixtures/m10_cann/README.md)。

### 业务数据、应用元数据与填充

设 `A(n,a)=ceil(n/a)×a`，H 为向量宽度。B 表示字节；下列复制长度属于应用载荷，尚未包括 UB 包头、协议控制包或多跳重复计数。

| 消息 | 业务字节 | 应用元数据 | 填充及实际复制长度 |
|---|---:|---:|---|
| 动态 INT8 Dispatch 向量 | H | 4 B scale + 12 B rank/token/topk 三元组 | 填充 `A(H,32)−H+28` B；实际复制 `A(H,32)+44` B |
| BF16 Combine 向量 | 2H | 0 | 实际复制 2H B |
| Dispatch 计数状态 | 0 | 32 B | 包括零贡献状态 |
| Combine 完成标志 | 0 | 32 B | 数据返回后的状态发布 |

Dispatch 数据窗口的地址步长为 `A(A(H,32)+44,512)`；Combine 为 `A(2H,512)`。地址步长尾部的空隙不会自动成为发送字节。状态消息属于应用元数据，不能计为业务归属，也不能混同 UB 的 ACK 等协议控制包。

`chunk_bytes` 只分割跨芯片向量消息；每个状态发布仍是单个 32 B store。集合通信分块、TA 分段与 TP 包载荷属于不同层次，后两者由[后端配置](../backend/README.md)控制。端口整包字节还受协议头、控制包与经过的交换出口数影响，不能直接当作唯一业务交付量；计数定义见[记录格式](record_storage.md)。

### 依赖、完成与并发

可选发送资源记录读取仿真器的实际调度队列和占用集合，能验证当前参考发送槽模型中的等待与释放；它不代表已测得硬件队列或发送引擎状态。就绪且满槽可以确定模型内的等待条件，不能单凭此项确定网络拥塞、协议确认等具体原因。记录口径见[发送资源状态](record_storage.md#端侧发送资源状态)。

同 rank 贡献保留专家计算及本地依赖。同芯片的不同 rank 通过本地交付事件和显式准备耗时连接，不生成 ns-3 链路、包或端口计数；这保持了跨芯片 Scale-Up 网络的研究范围。

跨芯片分块使用 `URMA_WRITE/RTP` 参考映射。Dispatch 数据由 Token 所属 rank 发往专家 rank，Combine 数据反向返回。发送方只提交一次事务，接收节点只登记等待。目标 `RemoteWriteCommitted` 累计达到实际载荷长度后表示数据可消费；源 `transaction_done` 才表示发送完成并释放发送资源。源数据发送完成后发布 Dispatch 计数；对应返回数据完成后发布 Combine 标志。不能将接收就绪与发送资源回收合并为一个回调。

旧模板的 `aiv_lanes` 为 1～48 个参考逻辑发送序列，不是测得的 AIV 分配数。每个序列等待前一事务完整结束才复用，另受每 rank 1 或 2 个发送槽限制。旧模板逐 rank 归约等待本地整个 Combine 阶段；这些行为保留供历史结果复现。

v0.5 模板按固定源码的 SplitToCore 分配贡献与 Token，保留 Dispatch 跨核数据栅栏及 Combine 核内次序，允许无关核独立工作和已到齐 Token 先归约。准备、重排、打包、状态检查与归约的累计成本保持原定义；它们的重叠可以改变总耗时。局部复用等待原生请求成功形成，默认复制栅栏等待目标数据交付，协议资源仍等待原生响应回收。该映射不等于模拟了真实 MTE、HBM 或缓存，来源与剩余假设见[固定源码核对](../fixtures/m18_a3_execution/README.md)，字段见[后端参考](../backend/README.md#a3-局部资源模板)。

URMA WRITE 是平台对通信行为的明确映射，不能据此声称复现原生 MTE/LDST。独立的 `cann_a3_v850_native_v1` 将向量映射为 ns-3-UB STORE 或 LOAD；状态和 LOAD 供数通知使用 STORE，并通过原生请求/响应回调判断数据与资源完成。LOAD 的 32 B 就绪通知、按 worker 均分任务和整包响应短尾填充均是明确的平台/后端参考行为，未经过 CANN 或 CM384 硬件校准。URMA READ、CTP、XCCL、CANN 超时、故障处理及原生标志复用竞态仍不在已验证范围。

### 通信缓冲区与输入限制

CANN 数据窗口下界保留 host tiling 的最大 2 字节类型预算，即使 Dispatch 使用 INT8。令 B 为每 rank 批次上限，E 为总专家数，K 为 Top-K，L 为参考逻辑发送序列数：

```text
data_min = 2 × B × [E × A(A(2H,32)+44,512) + K × A(2H,512)]
staging = 2 × L × A(A(H,32)+44,32)
cann_buffers = declared_data_window + 1,048,576 + staging
```

上述 `staging` 适用于旧模板。v0.5 模板按源码动态量化的占用公式与 170 KiB 阈值选择每核 1 或 2 个 Dispatch 缓冲，Combine 为 2 个缓冲；容量预留改为 `cores × [dispatch_buffers × A(A(H,32)+44,32) + 2 × A(2H,32)]`。数据窗口及各层保守求和规则继续保留。缓冲数依据源码公式，通信核数和本地处理耗时仍是参考参数。

声明的数据窗口必须不小于 `data_min`。容量模型还保留更大的向量及发送槽下界。令 R 为 rank 数，S 为每 rank 发送槽数，sD/sC 分别为 Dispatch/Combine 每元素字节数：

```text
communication_minimum_per_layer = max(
    B × R × min(K,R−1) × H × (sD+sC),
    2 × S × chunk_bytes,
    cann_buffers
)
```

完整模型将各 MoE 层下界保守求和，不假定跨层通信缓冲区可以重叠复用。该计算见 [model_capacity.py](../python/moe_ub_cosim/model_capacity.py)；它是容量预留规则，不能解释为每层实际发送了这么多字节。

当前 CANN 配置要求 H∈[1024,8192]、EP∈[2,768]、K∈[1,16]、B∈[1,512]、E≤1024；专家按 EP 顺序连续、等量放置。实际运行还受显式每图和累计预算限制。旧测试输入的 4096 次交付上限不代表所有新入口仍固定在该上限；声明更大预算也不等于已测量宿主内存或完成目标运行。

## 模型容量与 KV

容量计算使用模型结构、精度、专家放置和显式设备预算，分别扣除驻留权重及 scale、通信缓冲区和工作区，余量进入每 rank 唯一 KV 池。请求在接纳前按声明终点或有限观察片段预留 KV；驻留但本轮未选中的请求仍占容量，退出时只释放一次。不会把不足容量的请求悄悄截短。

旧 `model/1` 保留普通 GQA 布局；`model/2` 增加逐张量存储与 MLA 压缩 KV。对于当前 MLA 参考布局，每层缓存归一化后的 latent 向量及共享 RoPE key，不按注意力头数复制：

```text
KV_bytes_per_token = Σ层(latent_dim × latent_element_bytes
                         + rope_dim × rope_element_bytes)
KV_bytes_per_block = block_tokens × KV_bytes_per_token
request_reserve = ceil(reserved_token_count / block_tokens) × KV_bytes_per_block
```

普通已知长度请求的 `reserved_token_count=request.output−1`，即声明的最终总序列长度减 1；这里的 `request.output` 是调度器的总长度字段。真实路由观察片段使用 `prompt_tokens+first_decode_entry+decode_steps−1`，容量保证只覆盖所选片段，不将其改写为真实 `target_total_tokens`。预留不推进已计算长度，后续 `add_decode` 复用同一个块池，不再次分配相同容量。逐请求块对齐后再相加，不能先对平均长度取整。RoPE 表、临时展开或反量化矩阵、运行时元数据计入单独工作区预算。当前没有 HBM 带宽竞争模型，容量通过不表示量化内核已执行或实机可分配 HBM 已测得。R1 的固定张量形状、精度及生效预算见[场景容量参数](scenarios/cm384_r1.md#容量参数)，实现与接纳检查见 [mla_capacity.py](../python/moe_ub_cosim/mla_capacity.py) 和[容量测试输入](../fixtures/m10_mla_capacity/README.md)。

## 路由来源与观察范围

合成路由用于小型功能检查；公开模型路由用于保留真实专家选择分布。导入时验证文件身份、完整层/Token 顺序、专家范围和唯一性，不循环、补齐或拼接缺失记录。真实来源文件不包含本平台选择的网络、计算成本和线上请求到达过程，不能据此声称整个运行来自同条件实机采集。

Decode 入口与请求完成分别处理。文件尾端或观察片段结束均不推断 EOS；未知 `target_total_tokens` 保持未知。观察退出表示停止继续模拟该片段，不代表模型已生成结束标记。CM384/R1 的数据集固定版本、选择算法、Token 索引和精度差异见[场景路由说明](scenarios/cm384_r1.md#请求与专家路由)。

## 验证结论如何使用

| 依据 | 当前能支持的结论 | 不由该依据证明的内容 |
|---|---|---|
| 固定源码及逐项行为核对 | 贡献、字节、等待和资源规则有可追溯来源 | 原生内核时序、数值正确性和硬件性能 |
| Python/C++ 独立计划核对及小场景执行 | 输入约束、依赖、实际网络完成反馈、资源释放与观测不扰动执行 | 未运行的规模或不匹配配置 |
| [CM384/R1 WRITE 首轮验收](../fixtures/m10_research_slice/README.md) | 完整目标首轮依赖图、已保存图的后端重放、1 µs/10 µs 观测及记录守恒可重复 | 完整 64 步服务、稳态和目标硬件绝对时延 |
| [CM384/R1 原生首轮验收](../fixtures/m16_native_target/README.md) | STORE/LOAD 与逐包喷洒的完整首轮执行、42 所选端口聚合、分类来源和有限资源内公开导出 | 全端口详细记录、第二次完整原生目标重放或硬件校准；重放等价由独立小例检查 |
| [A3 资源与目标层段验收](../fixtures/m21_platform/README.md) | STORE/STORE、四类因果小测试、采集启停一致性，以及保留目标部署的 128/1024 请求连续三层执行和保存 | 新资源模板下的完整 61 块、64 轮服务或目标硬件校准；后端重放等价由独立小例检查 |
| 真实 R1-AWQ 路由和限定 A100 算子测量 | 相应路由来源真实、相应测量形状有设备依据 | CM384 INT8 部署同条件采集或 NPU 校准 |

`runtime_adapted` 表示固定通信源码行为已转为本平台计划；`full_model_graph_execution_validated=true` 只在相应完整图实际完成后写入。`unvalidated_reference` 仍表示未经目标硬件校准。`full_model_execution_validated`、`target_runtime_executed`、`numerical_model_executed`、`mla_kernel_validated`、`quantized_kernels_executed`、`target_deployment_validated` 等完整模型或原生执行标志仍为 false，不因图执行通过而改写。

开展参考参数实验时，应记录固定输入与版本、成本和网络选择、采样口径及结果适用范围。若要进一步声称复现目标硬件，需取得匹配形状、精度、并行布局和通信路径的测量，先固定事件对应、时间戳精度与字节口径，再分别比较数据量、阶段时间和端口分布。当前没有拟合目标时延或设定统一硬件误差百分比。

## 有限层段的初态与容量

有限层段复用原模型、完整部署容量、专家放置、原始路由层号和所选 Decode 步骤上下文。假定首块输入及 KV 已就绪，从空网络开始；首层不自动等同于充分预热，也不宣称稳态。只执行选定完整块的参考成本，不含 embedding、末尾 head 或采样。局部读完成采用全部原生 STORE 请求已形成的参考事件，业务与状态到达按真实接收字节区间核验；硬件 MTE/HBM 时序仍未校准。公开流程证据见 [M20](../fixtures/m20_segment_facts/README.md)，真实路由的目标规模执行与剩余参考假设见 [M21](../fixtures/m21_platform/README.md)。
