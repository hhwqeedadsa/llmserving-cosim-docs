# M10：固定 CANN 源码路径的小型行为模型

本步新增 `cann_a3_v850_fullmesh_v1`，从逐 Token、逐专家贡献生成 Dispatch 数据、计数发布、Combine 数据与完成标志。计划经过 Python 和 C++ 分别重建校验，由既有 ASTRA/同一 ns-3-UB 网络执行。旧 `direct_rank_dedup_v1` 保留原语义。

这不是原生 CANN 执行或完整 M10 验收。源码匹配项、参考调度及耗时分开记录；模型仍为 `unvalidated_reference`。仅模拟通信与计算依赖、形状和字节，不计算量化或加权归约的数值张量。

## 源码与调用配置

公开仓库为 [CANN ops-transformer](https://gitcode.com/cann/ops-transformer)，固定 v8.5.0 对应提交 `6ead121aded45355043b502756b6592fd7c30b14`。[source-evidence.json](source-evidence.json) 保存选定文件、SHA-256、大小、调用配置及 LICENSE 摘要。源文件只读保留在本机 runs/v03-cann-behavior/cann-source，不加入依赖或提交其源码。该仓库使用 CANN Open Software License Agreement Version 2.0；本仓库实现的是独立声明式行为模型，没有链接或执行其内核。

选定 A3 的 MoeDistributeDispatchV2/MoeDistributeCombineV2 非 fullmesh_v2 分支，API 配置 `fullmesh_v1`、TP=1、Dispatch dynamic INT8（quant_mode=2、无 smooth_scales）、Combine 非量化 BF16（quant_mode=0）、expertShardType=0。排除专用 shared expert rank、弹性与特殊专家、二维专家 mask、fullmesh_v2、其他架构/量化和 TP。各 rank 使用相同物理输入槽数，以前缀 active mask 表达实际批次；活跃数为零不宣称原生算子允许 BS=0。

以下路径均相对固定提交的 mc2/，D 表示 moe_distribute_dispatch_v2，C 表示 moe_distribute_combine_v2。可通过[固定提交](https://gitcode.com/cann/ops-transformer/tree/6ead121aded45355043b502756b6592fd7c30b14)核对。

| 行为 | 源码定位 | 本步表达及边界 |
|---|---|---|
| 具体内核分支 | D/C 的 op_kernel/*_v2.cpp | A3、非 fullmesh_v2、所列量化/TP；不会把 HCCL 通用接口当成实际路径。 |
| Dispatch 复制与地址 | D/op_kernel/moe_distribute_dispatch_v2.h:379–386、545–565、653–714 | 每 Token×Top-K 单独贡献，按连续均衡分核顺序发送；同目的 rank 的多个专家不去重。 |
| 计数、可见性及等待 | 同文件 SetStatus:931–970、WaitDispatch:1144–1190、Process:1470 起 | 每源 rank 向每专家发一份 32 B flag/count，包括零贡献；本 rank 的数据发送完成后发布，目标等待本地专家的全部源计数及数据后重排。SyncAll 是本地核同步，不增加全 EP 逐层屏障。 |
| 目标排列及返回 | 同文件 LocalWindowCopy:1242 起；C/op_kernel/moe_distribute_combine_v2.h 的 BuffInit、ExpertAlltoAllDispatchCopyAdd:834–868 | 目标按本地专家、源 EP 序号、源 Token 排列；Combine 均衡分核并在核内按 EP 序号旋转，每贡献返回 BF16 向量后发布 32 B 完成标志。 |
| Token 归约 | C/op_kernel/moe_distribute_combine_v2.h 的 LocalWindowCopy、Process:1322 起 | Token 所属 rank 等待其 Top-K 结果后加权归约；远端专家 rank 不先合并同 Token 的多个专家结果。当前图保守地等待该 rank 的整个 Combine 阶段，不模拟逐 Token 归约流水重叠。 |
| 缓冲 | D/op_host/op_tiling/moe_distribute_dispatch_v2_tiling.cpp 的 CheckWinSize:950–984；两内核 BuffInit | 保留数据窗口、1 MiB 状态窗口和 staging 预算；当前图完成前不复用，未模拟原生双窗口流水重叠。 |

## 字节、资源和耗时合同

设 `A(n,a)=ceil(n/a)*a`，H 为 hidden_dim。INT8 Dispatch 实际复制长度为 `A(H,32)+44`：H 字节业务向量、4 字节动态 scale、12 字节 rank/token/topk 三元组；其余 `A(H,32)-H+28` 字节为填充/保留区域。窗口地址步长为 `A(A(H,32)+44,512)`，步长尾部空隙不自动传输。BF16 Combine 实际复制 `2H` 业务字节，窗口步长 `A(2H,512)`。两类 32 字节状态发布均记为应用元数据，不能算作业务归属或 UB 协议控制包。

同 rank 贡献只进入计算和本地阶段依赖；同 chip 不同 rank 使用本地交付事件及显式准备耗时；跨 chip 的每个分块只提交一份实际 URMA_WRITE/RTP 事务，RECV 只登记等待。`chunk_bytes` 只分割跨 chip 向量消息，状态发布保持单个 32 B store。本模板的应用字节经同一 UB 包格式、TA/TP 分段与链路排队执行；这是已声明的参考事务映射，未经验证不能等同原生 MTE/LDST。

`aiv_lanes` 是 1～48 的参考逻辑发送 lane 数，不是测得的 AIV 分配。每 lane 等待前一次事务完整完成后复用；另受每 rank 1 或 2 个发送槽限制。数据就绪由本次目标写完成产生，源资源由 transaction_done 回收。该保守串行规则强于原生部分 MTE 流水，不能据此声称时序等价。

窗口下界继承 host tiling 的最大 2 字节类型预算，即使 Dispatch 使用 INT8：

```text
data_min = 2 * maxBS * (E * A(A(2H,32)+44,512) + K * A(2H,512))
staging_reference = 2 * aiv_lanes * A(A(H,32)+44,32)
total = declared_data_window + 1048576 + staging_reference
```

E 为总专家数，K 为 Top-K，maxBS 为配置的每 rank 批次上限。模型容量按整个层段逐层保守求和，仍只使用每 rank 一个原生 KV 池。H∈[1024,8192]、EP∈[2,768]、K∈[1,16]、E≤1024 且按 EP 顺序等量连续放置；同时受现有小图 4096 transfer 等预算限制。这些输入界限不是目标规模已通过的证明。

`cann_profile.local_cost_ns` 明确声明重排、计数检查、结果打包和归约的参考耗时；prepare 覆盖 attention/路由/量化准备，专家计算仍用原线性参考项。同专家总输入增加会增加其计算时间；相同总量的不同分布尚不使用实测 grouped kernel。`expert_local_merge=0` 防止沿用旧远端合并。当前 CANN 模板明确拒绝 operator_profile，因为 M9 的测量阶段和维度不覆盖本路径；没有套用 A100 小表或静默外推。

仅表达成功路径的状态发布与等待；本地轮询/清零的成本归入参考阶段，不逐次生成访问；超时探测、故障处理、原生标志复用竞态及重传未验证。NPU、原生 CANN 阶段时序、端口分布与完整模型数值均未校准。

## 可运行输入与验收

[service.json](service.json) 与 [layers.json](layers.json) 使用两个三 rank 组、三层、七请求、H=1024、六专家 Top-2、BF16 计算/Combine 与 INT8 Dispatch。容量为明确命名的 synthetic GQA 小模型，八个可用 KV block；路由为合成非均匀记录，不是 R1。[layer.json](layer.json) 的 H=1025 用于非对齐向量检查。

在已按当前任务构建的目录上运行：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m10_cann/service.json --layer fixtures/m10_cann/layers.json \
  --backend build/m10-cann-behavior/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m3_shared_bottleneck/network \
  --observation fixtures/m3_shared_bottleneck/observation.json \
  --output-dir runs/cann-example

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_cann_behavior.py \
  --build-dir build/m10-cann-behavior --output-dir runs/cann-check
```

输出目录须不存在。首次实际运行见 runs/v03-cann-behavior/service-first；最终检查见 runs/v03-cann-behavior/cann-final/summary.json 与 commands.json。

最终基准五轮完成 36 条逐层路由、72 份专家贡献（同 rank 14、同 chip 21、跨 chip 37），有 126 次零计数发布和 5 次多个专家命中同一目的 rank。执行 305 次 UB 写及 123 次本地交付，业务 113664 B、应用元数据 5616 B、填充 1036 B；全部输入、输出字节、依赖、资源回收和端侧记录独立核对。10 处层间重叠说明没有强制全 rank 逐层屏障。

| 对照 | 结束 ns | UB 写次数 | 检查结果 |
|---|---:|---:|---|
| 两组共享 10 Gbps | 131530 | 305 | 两组首轮及下一轮提交均晚于各自独立运行。 |
| 链路 1 Gbps | 1214814 | 305 | 字节不变，真实排队与后续批次均后移。 |
| chunk=257 | 176178 | 638 | 总业务、元数据、填充不变。 |
| 每 rank 一个发送槽 | 143044 | 305 | 实际峰值 WQE≤1。 |
| 一个参考 lane | 144155 | 305 | 发送顺序/并发改变执行时间。 |
| 局部参考成本×100 | 475621 | 305 | 依赖反馈随局部计算变化。 |
| 全部同 chip | 13330 | 0 | 354 次本地交付，专家贡献保留。 |

共 13 次服务运行覆盖独立组、共享组、空输入、场景 YAML、来源开/关/不挂观测；来源开关的结果与原生网络日志完全相同。按 1 μs/10 μs 采样重新导出，业务归属总量不变，观测目录只有公开字段。H=1025 的两个专家贡献在 chunk=1024/17 下分别产生 20/382 次实际写，均为 6150 B 业务、352 B 元数据、118 B 填充；包内归属不包含 scale、三元组、状态或填充。3 项前端配置和 5 项后端计划篡改均拒绝，后端拒绝时仿真时间为 0、无已提交传输。

完整命令、两次开发检查失败与修复、95 项 Python 单元测试、C++ 检查及旧功能回归见 [环境记录](../../docs/environment.md)。M10 余项及停止位置见 [current.md](../../docs/tasks/current.md)。
