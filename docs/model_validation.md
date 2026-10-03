# M5.3：目标通信行为核对

以下 M5.3 章节保留 2026-09-29 的历史结论；2026-10-03 的 M9 算子测量补充见文末，不改写当时的未验证状态。

2026-09-29，本轮完成了 **DeepEP v1.2.1 单机普通 Dispatch/Combine 路径的源码核对**，以及现有参考模型的业务字节和连续运行检查。没有运行 DeepEP，没有取得目标设备测量或通信轨迹。`direct_rank_dedup_v1` 仍为 `reference_model`，校准状态仍为 `unvalidated_reference`。

固定来源、摘要、配置、八项结论及本地证据保存在 [audit.json](../fixtures/m5_model_audit/audit.json)，检查入口见 [复查说明](../fixtures/m5_model_audit/README.md)。本记录按规范第 6.2、6.7、9.2、13.7、13.9 节区分源码依据、功能测试和目标系统验证；未改变技术定义或运行规则。

## 目标版本和调用范围

采用官方 [DeepEP v1.2.1 提交](https://github.com/deepseek-ai/DeepEP/tree/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee)，完整 SHA 为 `9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee`。保存了 13 个公开文件的 SHA-256、Git blob SHA-1、大小和固定 URL，下载内容与该提交的 Git tree 一致。这些文件只供审阅，没有成为编译依赖；上游测试仅阅读，未执行。

| 配置项 | 本次固定值 |
|---|---|
| 路径与参与组 | normal intranode，4 rank；要求四 GPU 经 NVLink 互相可见；未验证本机满足该部署条件 |
| 专家与形状 | 8 专家，Top-K=2，hidden=4096；专家 0/1、2/3、4/5、6/7 分别在 rank 0、1、2、3 |
| 数据 | Dispatch、Combine 均 BF16；专家索引 int64、Dispatch 权重 FP32；没有 FP8 scales |
| Dispatch 调用 | 每次 `handle=None`，同时提供索引与权重；`expert_alignment=1`，`num_worst_tokens=0` |
| Combine 调用 | 使用本次 Dispatch 的 handle；`topk_weights=None`，`bias=None` |
| 流依赖 | `previous_event=None`、`async_finish=False`、`allocate_on_comm_stream=False` |
| 缓冲区 | `num_nvl_bytes=134217728`，`num_rdma_bytes=0`，`low_latency_mode=False`；这是核对配置，未在 GPU 分配或测量 |
| 通道与参数 | 20 SM，10 通道；Dispatch `Config(20,6,256,6,128)`，Combine `Config(20,9,256,6,128)` |
| 调用者约定 | 专家结果已由调用者加权、合并为每个 Dispatch 接收行一份向量，再交给 Combine；未取得调用者执行证据 |

Config 中的 6/9 是各通道、对端的发送 Token 块上限，256 是接收环容量。表中记录构造参数；末尾 RDMA 接收数 128 会在构造器中对齐到 132，但本次单机路径不使用 RDMA。依据为 [Python 配置表][config]、[Config 定义][config-hpp]和 [C++ 缓冲区检查][dispatch-runtime]。

排除跨机、低延迟、FP8、缓存 Dispatch handle、异步重叠、初始化耗时、专家数值前向及硬件性能结论。选择此路径便于检查去重与回传规则，不表示它使用 UB，也不表示当前 UB 仿真已复现 NVLink。

## 八项行为及差异

本地生成规则集中在 [plan.py](../python/moe_ub_cosim/plan.py) 的 `build_plan`；实际写事务在 [ub_write_transfer.cc](../backend/ub_write_transfer.cc)，完成事件在 [write_completion.cc](../backend/write_completion.cc)，跨层依赖在 [multilayer.py](../python/moe_ub_cosim/multilayer.py)。逐项的文件摘要和位置另存于 audit.json。

| 核对项 | 目标源码依据和行为 | 当前参考模型及结论 |
|---|---|---|
| 通信参与者 | 四 rank 都参加通知；没有本地 Token 的成员仍交换计数、发布通道边界并同步。[初始化][init]、[通知][notify]、[Dispatch][dispatch] | 零 Token 成员处理必要远端专家工作；完全空闲成员不产生填充计算。未建模目标通知控制与屏障，属于部分一致；没有执行目标零 Token 测试。 |
| 数据表示 | BF16 向量每份 8192 字节。非缓存 Dispatch 还写源索引、Top-K 索引与权重；所选 Combine 仍写源索引。[Dispatch][dispatch]、[Combine][combine] | 样例声明 FP16，同为每元素 2 字节，应用元数据为零；不执行张量数值运算。只能比较向量字节，不能宣称 dtype 数值或总链路字节一致。 |
| 复制与合并 | 每 Token 按目的 rank 去重，按专家分别计数。Combine 接收 Dispatch 行布局并相加，库本身不对激活乘路由权重。[布局][layout]、[API][api]、[归约][combine] | 保留全部专家计算，按目的 rank 发一份输入，参考局部加权合并后回传一份向量。去重一致；回传量一致以调用者已按约定合并为条件，加权结果未验证。 |
| 传输对端 | 所选路径直接访问目的 GPU 缓冲区；本地行也在本地缓冲区处理，没有中间 GPU 转发。[Dispatch][dispatch]、[Combine][combine] | 远端逻辑对端一致，本地专家不产生网络写。网络为 UB 上的 URMA WRITE/RTP；NVLink 内存操作和 UB 包过程不可等同。 |
| 分块与发送顺序 | 按通道划分 Token 范围，10 通道，各对端独立处理，Dispatch/Combine 块上限 6/9 Token。[分区][partition]、[配置][config]、[内核][dispatch] | 有向字节段按 8192 字节分块，另测 3072；SEND 按目的 rank、分块号排序。已知不同，不能用字节分块或 UB 包大小代表目标通道顺序。 |
| 并发与缓冲区 | 各通道/对端使用 256 Token 环形缓冲；发送者发布 tail，接收者推进 head 后空间可复用，并占用 GPU SM。[Dispatch][dispatch]、[Combine][combine] | 每 rank 使用 1 或 2 个 SEND 槽，当前连续样例为 1；由发送事务完成释放，另受 UB 资源限制。没有目标通道、环占用或 SM/HBM 竞争模型。 |
| 同步与结束 | Dispatch、Combine 通知中有真实组屏障；当前参数下计算流等待通信流。非缓存 Dispatch 还在主机等待接收计数；Python 返回不等于 GPU 全部完成。[通知][notify]、[Combine 通知][combine-notify]、[组屏障][barrier]、[流等待][dispatch-runtime] | 参考本地阶段等待规定的发送、接收和计算；没有目标通知屏障。接收数据就绪与发送事务完成分别处理。实际阶段映射未建立，不能比较阶段时序。 |
| 端侧处理 | 布局、拷贝、索引转换、归约由 CUDA/主机代码执行；专家 GEMM、局部加权属于调用者。[布局][layout]、[Dispatch][dispatch]、[Combine][combine] | 用逐层固定 COMP 耗时表示准备、专家和合并；专家耗时为该 rank Token–专家配对数乘单份时间。处理项目有对应，但时长、重叠及未来 profile 是否重复计时均未验证。 |

同一 Token 命中同 rank 两个专家时，目标 Dispatch 接收行仍是一行，专家计数为两份。实际专家执行需要调用者使用索引重排、计算和合并；不能把 DeepEP 的通信正确性检查解释为已完成专家模型前向。

## 字节口径与独立核对

独立 CPU 计算按目标源码的布尔 rank 掩码统计 `n_ij`，包括本地对角线；专家计数另外按每次选择累加。投影为跨 rank 业务传输时才排除 `i=j`。每个远端行的 Dispatch 主体为 `4096 × 2 = 8192` 字节；在上述调用者约定下，Combine 返回一份同宽度向量。该检查未导入、编译或执行 DeepEP。

对每个目的接收行，源码中的 Dispatch 元数据逻辑存储为 `4 + 2×8 + 2×4 = 28` 字节；当前 Combine 参数下为 4 字节源索引。它们不包含通知计数、屏障信号、通道边界、head/tail 更新、物理事务和协议开销，**不能作为实测线速字节数**。这些额外字节未补入当前参考模型。

| 输入 | 远端去重行 / 本地行 | 两阶段业务字节 | 目标逻辑元数据推导值：Dispatch / Combine |
|---|---:|---:|---:|
| 规范四 Token 基础例 | 7 / 1 | 114688 | 196 / 28 |
| 首 Token 改为同远端 rank 两专家 | 6 / 1 | 98304 | 168 / 24 |
| 三层、三次解码推进的主运行 | 39 / 18 | 638976 | 1092 / 156 |
| 稀疏请求连续运行 | 21 / 6 | 344064 | 588 / 84 |
| 32 层、两次解码推进 | 279 / 127 | 4571136 | 7812 / 1116 |

元数据列仅按跨 rank 行推导；本地缓冲区操作没有计入网络。业务字节与每次实际 UB 唯一交付分别核对，不能与交换端口整包计数混用。

## 实际检查与结论范围

2026-09-29 的执行记录位于 `runs/m5-3-model-audit-20260929/`，命令、输入、退出状态和路径见 [环境记录](environment.md)。

- 13 个目标文件的 SHA-256、Git blob SHA-1、大小和引用行范围全部通过，本地审阅文件摘要一致。
- 独立布局检查通过 117 个参考计划：基础、同 rank 多专家、字节分块、零 Token 成员、纯本地五例，加上 4 个源 rank × 28 种专家对的穷举。另检查空布局算式；空服务不提交空层图。
- 8 次研究运行共 130 个层实例的专家输入、业务矩阵与真实 UB 传输逐条一致。主例 78 次写、638976 字节，完成于 193789 ns；这些是本参考配置的仿真值。
- 现有多层回归通过：重复、来源关闭、零时钟、无观测、链路延迟、稀疏到达、分块与 32 层连续执行；10 个预期错误均明确拒绝。观测开关不改变执行，依赖、资源结束与来源守恒保持成立。

| 状态项 | 本轮结果 |
|---|---|
| 参考模型功能检查 | 通过 |
| 目标八项源码核对 | 完成；包括部分一致、已知差异与未验证处理耗时 |
| 目标实现执行 / 行为完整复现 | 未执行 / 未完成 |
| 专家加权及完整模型数值结果 | 未验证 |
| 目标阶段时序 | 未验证；没有目标执行轨迹及事件映射 |
| 目标端口时窗 | 未验证；NVLink 与 UB 观测位置、计数口径尚无对应依据 |
| 设备计算耗时 | 未验证；没有本配置的设备或调用者测量 |
| 参数拟合 / 误差阈值 | 未拟合 / 未设定 |
| 模型状态 | `reference_model`、`unvalidated_reference`、`full_model_execution_validated=false` |

当前连续输入是 `synthetic_indexed_file` 合成路由，计算为 `per_layer_fixed_reference`；执行范围是连续 MoE 层片段，其迭代时延不是完整模型 ITL 或 TTFT。可以在这些明确假设下研究操作和端口观测的关联，不能用于预测 DeepEP/NVLink 性能或宣称目标 NPU 已校准。

未来取得目标记录后，需要在拟合前固定事件对应、时间戳精度、采样窗口和各自的数据量/阶段时间/端口分布准则；本轮没有预设统一误差百分比，也没有用平均时延代替分项验证。真实路由、A100 计算测量、多请求批处理和具体通信库复现仍在 [后续候选清单](future_iterations.md)，尚未开始。M5.3 的核对任务至此完成，以上缺项保留为模型边界。

[init]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/deep_ep/buffer.py#L30-L125
[api]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/deep_ep/buffer.py#L276-L432
[config]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/deep_ep/buffer.py#L220-L273
[config-hpp]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/config.hpp#L18-L42
[layout]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/layout.cu#L17-L105
[notify]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/intranode.cu#L11-L103
[dispatch]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/intranode.cu#L164-L457
[combine-notify]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/intranode.cu#L509-L556
[combine]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/intranode.cu#L577-L876
[barrier]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/utils.cuh#L487-L518
[partition]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/kernels/utils.cuh#L410-L415
[dispatch-runtime]: https://github.com/deepseek-ai/DeepEP/blob/9af0e0d0e74f3577af1979c9b9e1ac2cad0104ee/csrc/deep_ep.cpp#L387-L539

## 2026-10-03：M9 算子依据补充

在 NVIDIA A100 80GB PCIe、PyTorch 2.8.0+cu126/CUDA 12.6 上实际测量了 22 项 H=F=256、float16 的串行 gated FFN 与 GQA SDPA MATH 形状。CUDA Event 原始重复样本、完整方法及软件/脚本/输入摘要在 runs/v03-route-compute-evidence/a100-profile.json；入口、精确适用范围和参考残差见 [M9 说明](../fixtures/m9_evidence/README.md)。输入/权重为 seed 21 的随机算子张量，没有采集模型路由。

实际 ASTRA/UB 执行按本次上下文和逐专家输入向量使用表值，C++ 独立重算及篡改拒绝通过。同总量 `[4,0]`/`[2,2]` 的专家测量及仿真节点分别为 52327/102196 ns，通信量相同；该方向取决于当前串行实现，不表示所有专家热点都会更快或更慢。未覆盖形状拒绝，不做峰值算力折算或静默线性外推。

| 状态项 | M9 本次结果 |
|---|---|
| 路由消费、窗口退出与 KV 记账 | 人工来源文件的功能检查及实际 UB 执行通过；未知目标不改为窗口长度。 |
| 真实专家路由 | 用户登录后取得固定版本的两份 R1-AWQ 原文件；14848 条 Decode 层记录逐条核验，选定 12 条、96 份专家输入经过四次实际 UB 对照。采集环境未知项、AWQ 与目标 INT8 的差异保留。 |
| A100 算子依据 | 上述 22 项实际测量并用于仿真；包括 eager 发射间隙，非生产 grouped/融合内核或完整模型。 |
| 未测阶段、通信行为、完整模型及目标 NPU | prepare 残差/局部合并等仍为参考；DeepEP/CANN 未执行，R1/MLA 和 CM384 规模尚未验证。 |
| 校准状态 | `unvalidated_reference` 不变；不以 A100 小算子或合成输入替代目标硬件同条件校准。 |

M9 已完成本步的真实数据导入和算子依据验收，文件与选择清单见 [M9 说明](../fixtures/m9_evidence/README.md)。真实 R1 对照是选定 routed 层的通信重放，使用参考计算，不含原生服务容量验证；没有把小型 GQA 容量/算子表改名为 R1。完整 R1/MLA/shared expert 及目标部署组合仍未实现或验证。当前停止位置见 [current.md](tasks/current.md)，完整命令及结果见 [environment.md](environment.md)。
