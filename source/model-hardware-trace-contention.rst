模型、硬件拓扑、Trace 与网络竞争
====================================

.. meta::
   :description: Qwen3 MoE 模型与硬件参数、LLMServingSim 输入输出规模、Block 24 网络竞争、trace 到 ns-3 的流生成逻辑、CCL 实现和 384 NPU 候选映射。

本章完成 6 组 LLMServingSim 请求规模实验和 11 组 ns-3-UB 网络竞争实验，把
``模型/请求 → batch → block → collective → peer flow → task/WQE → packet/hop/queue``
整理为一条可追溯证据链。计算 Profile 来自 RTX PRO 6000 Blackwell Server Edition，
不是 A100；100/400 Gbps 拓扑是受控仿真参数，不是华为 384 超节点实测。

结论摘要
--------

* 输入从 128 增至 4096 tokens，TTFT 从 30.887 ms 增至 202.702 ms；输出从 4 增至
  32 tokens 不改变同一输入的 TTFT，主要按 8.08--8.35 ms/token 累积请求完成时间。
* 4096-token prompt 超过 2048-token 调度预算，触发两个 prefill chunks，并产生
  98.214 ms queuing delay；它不是简单的线性算力放大。
* 同向 EP 竞争把目标流 FCT 从 23.231 μs 提高到 45.683 μs（+96.65%），反向 EP
  仅提高到 23.731 μs（+2.15%）。
* 固定总 bytes 和 task 数，只把目的端从均衡改为 97% 热点，phase 从 23.991 μs
  增至 88.935 μs，最大队列从 21,366 B 增至 813,689 B。
* 当前最细可观察 4 KiB packet、逐 hop 驻留、queue occupancy 和 port throughput；
  peer/channel/chunk 仍缺真实 NCCL/HCCL/DeepEP runtime 证据。

证据分类
--------

.. list-table:: 本页使用的证据标签
   :header-rows: 1
   :widths: 20 35 45

   * - 标签
     - 来源
     - 允许的结论
   * - ``measured``
     - 实机 profiler、NIC counter、packet capture
     - 硬件直接观测；本轮没有新增网络 measured artifact
   * - ``profile-derived``
     - RTXPRO6000 GPU Profile 库
     - 指定模型、shape 和 Profile 下的 operator latency
   * - ``trace-derived``
     - LLMServingSim trace 或 ns-3 trace
     - collective type/bytes、事件字段和守恒统计
   * - ``algorithm-projected``
     - ring 等 collective 算法展开
     - phase、src、dst、peer payload；不是 runtime 实测排程
   * - ``analytical``
     - ASTRA analytical network
     - 16 GB/s、20 μs 假设下的 collective timing
   * - ``ns3-simulated``
     - OpenUSim/ns-3-UB
     - 登记拓扑中的 flow、packet、queue 和 port 结果

一、模型、输入和硬件位置
------------------------

模型为 ``Qwen/Qwen3-30B-A3B-Instruct-2507``：48 层、hidden 2048、32 个 attention
heads、4 个 KV heads、head dim 128、128 experts、Top-8、MoE intermediate 768、
BF16、最大上下文 262,144 tokens。

.. list-table:: 模型大小与状态大小
   :header-rows: 1
   :widths: 30 25 45

   * - 物理量
     - 数值
     - 来源/边界
   * - 总参数量
     - 30,532,122,624（30.532B）
     - 模型 JSON 权重维度求和，``config-derived``
   * - 每 token 激活参数量
     - 3,353,032,704（3.353B）
     - 共享参数 + 每层 Top-8 experts
   * - BF16 全权重
     - 61.064 GB / 56.871 GiB
     - 参数 × 2 B；不是显存实测
   * - KV cache / token
     - 98,304 B
     - 48 层 K/V、4 KV heads、head dim 128
   * - 128 / 1024 / 4096 tokens KV
     - 12 / 96 / 384 MiB
     - 容量推导，不包含 allocator overhead

不同位置的计算和带宽必须分开记录：

.. list-table:: 本轮计算与链路参数
   :header-rows: 1
   :widths: 24 25 23 28

   * - 位置
     - 本轮参数
     - 调节入口
     - 不能证明什么
   * - GPU operator
     - RTX PRO 6000 Blackwell BF16 Profile
     - hardware profile/model/shape
     - 不是 A100 或华为 NPU 时间
   * - LLMServingSim logical fabric
     - FullyConnected、2 ranks、16 GB/s、20 μs
     - cluster ``link_bw`` / ``link_latency``，生成 ``network.yml``
     - 没有 packet queue 和路由冲突
   * - Block A/B host access
     - 400 Gbps、20 ns
     - ns-3 ``topology.csv``
     - 不是 NVLink/真实 NIC 校准
   * - Block A/B inter-switch core
     - 100 Gbps、50 ns
     - ns-3 ``topology.csv``
     - 只用于隔离共享 core 瓶颈
   * - Block C destination access
     - 100 Gbps、20 ns
     - ns-3 ``topology.csv``
     - 只用于隔离目的端热点
   * - Block C inter-switch core
     - 400 Gbps、50 ns
     - ns-3 ``topology.csv``
     - 不等于华为超节点内带宽

二、输入/输出规模实验
------------------------

固定 Qwen3 MoE、TP=2、EP=2、RTXPRO6000 Profile、ASTRA analytical 16 GB/s/20 μs、
单请求和 ``max_num_batched_tokens=2048``，只改变输入 128/1024/4096 和输出 4/32。

.. figure:: _static/round09/llmservingsim-input-output-sweep.svg
   :alt: 输入长度与 TTFT TPOT 请求完成时间的关系
   :align: center
   :class: study-figure

   **图 1：输入长度主要抬高 TTFT，输出长度主要累积请求时延。** 虚线为
   2048-token 调度预算；4096-token prompt 因 chunked prefill 多出一个 batch iteration。

:数据来源: ``research/round09/llmservingsim_input_sweep.csv``；原始请求 CSV 与文本 trace。
:物理量与单位: 横轴为 input tokens；纵轴为 TTFT（ms）、TPOT（ms/token）和 request latency（ms）。
:证据类型: ``simulator-output + profile-derived + analytical``。
:解释边界: 不能据此声明 A100/NPU 的绝对指标，也看不到 packet queue。

.. list-table:: 六组请求结果
   :header-rows: 1
   :widths: 10 10 16 16 18 18 12

   * - Input
     - Output
     - TTFT (ms)
     - TPOT (ms)
     - Request end (ms)
     - Queue delay (ms)
     - Batch traces
   * - 128
     - 4
     - 30.887
     - 8.080
     - 55.127
     - ≈0
     - 4
   * - 128
     - 32
     - 30.887
     - 8.081
     - 281.413
     - ≈0
     - 32
   * - 1024
     - 4
     - 60.947
     - 8.153
     - 85.407
     - ≈0
     - 4
   * - 1024
     - 32
     - 60.947
     - 8.155
     - 313.760
     - ≈0
     - 32
   * - 4096
     - 4
     - 202.702
     - 8.348
     - 227.747
     - 98.214
     - 5
   * - 4096
     - 32
     - 202.702
     - 8.349
     - 461.530
     - 98.214
     - 33

每个 scheduler iteration 对 48 层各生成 48 次 AllReduce、48 次 AllGather 和
48 次 ReduceScatter。例如 128+4 共 4 个 batch traces、576 个 collective nodes；
4096+4 因两个 prefill chunks 加 3 个 decode iterations，共 5 个 batch traces、720 个
collective nodes。这些是 logical collective，不是 NIC wire bytes。

三、Block 24 网络竞争
------------------------

目标 probe 是 Block 24 MoE AllGather 的 ``rank 0 → rank 1`` peer leg，payload 为
278,528 B。该 peer leg 来自两 rank ring 投影；``URMA_WRITE`` 是 ns-3 transport
projection，不是“真实 NCCL 使用 UB”的声明。

方向与流类型
~~~~~~~~~~~~

.. figure:: _static/round09/block24-direction-contention.svg
   :alt: 同向反向EP和KV竞争下的目标流FCT阶段时间及最大队列
   :align: center
   :class: study-figure

   **图 2：同向共享瓶颈接近翻倍，反向流影响很小。** 同向 KV 中目标 FCT 与同向 EP
   接近，但更大的 KV proxy 把 phase tail 拉到 66.012 μs。

:数据来源: ``research/round09/network_contention_summary.csv`` 的 ``a0--a3``。
:物理量与单位: 目标流 packet-envelope FCT、phase completion（μs）和最大 queue（KiB）。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: 同向 KV 是 524,288 B proxy；不是实机 KV transport 测量。

同向 EP 的目标 FCT 为 45.683 μs（+96.65%）；反向 EP 为 23.731 μs（+2.15%）。
这验证当前全双工路径中的方向隔离，也说明分析时必须同时记录 ``src、dst、direction``，
不能只记录“有多少背景 bytes”。

竞争字节比
~~~~~~~~~~

.. figure:: _static/round09/block24-background-byte-ratio.svg
   :alt: 竞争字节比与目标流FCT最大队列的关系
   :align: center
   :class: study-figure

   **图 3：25%--100% 同向竞争字节下，目标流 FCT 近似线性增长。** 百分比是竞争流
   bytes / 目标流 bytes，不是稳态链路利用率。

:数据来源: ``research/round09/network_contention_summary.csv`` 的 ``a0、b1--b4``。
:物理量与单位: 竞争字节比（%）、目标流 FCT（μs）和最大 queue（KiB）。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: ``R²=0.9999`` 仅适用于当前同时释放、同向、同路径的 5 个控制点。

目标 FCT 依次为 23.231、28.922、34.609、40.291、45.683 μs。当前条件下，竞争流
覆盖目标流的持续时间随 bytes 增长，形成近似线性恶化。

目的端倾斜
~~~~~~~~~~

.. figure:: _static/round09/block24-destination-skew.svg
   :alt: 均衡Zipf热点目的端分布的阶段时间和最大队列
   :align: center
   :class: study-figure

   **图 4：总 bytes 和 16 个 task 固定，只改变四个目的端份额。** 97% 热点令 phase
   达到 88.935 μs，是均衡分布的 3.71 倍。

:数据来源: ``research/round09/network_contention_summary.csv`` 的 ``c0--c2``。
:物理量与单位: phase completion、task FCT P95（μs）和最大 queue（KiB）。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: 这是目的端分布的因果实验，不是实际 MoE router token histogram。

balanced/Zipf/hot 的 phase 为 23.991/44.586/88.935 μs，最大队列为
21,366/261,488/813,689 B。结果说明总通信量相同不代表 tail 相同，局部目的端 access
link 可以成为真正瓶颈。

微秒级流量与带宽占用
~~~~~~~~~~~~~~~~~~~~

FCT 只给出最终耗时。为了看到“每个微秒链路被谁占用”，本页进一步读取 data packet 的
精确端口 egress 时间和 wire size，把每包在 100 Gbps 链路上的序列化区间与 0.25 μs
时间分箱求交。它不是按 packet 起点简单计数，因此单方向不会因 bin 边界虚假超过容量。

.. figure:: _static/round09/block24-core-bandwidth-timeseries.svg
   :alt: 四种竞争场景中100Gbps核心链路的微秒级流量和带宽占用
   :align: center
   :class: study-figure

   **图 5：四个竞争 case 的核心链路时间序列。** 正值是左到右，负值是右到左；
   目标 AllGather 与 EP/KV 竞争流用不同颜色堆叠，并标出 target/phase last-ACK。

:数据来源: ns-3 ``AllPacketTrace`` 的 task 身份、``PortTrace_node_8/9_port_4.tr`` 的精确 egress 时间；汇总为 ``research/round09/network_core_bandwidth_timeseries.csv``。
:物理量与单位: 横轴为仿真时间（μs）；纵轴为 0.25-μs bin 内 data-packet wire bandwidth（Gbps）；4096-B payload 对应 4174-B wire packet。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: 不含小尺寸 ACK/control 带宽；100 Gbps 是代表性 core 参数；0.25 μs 是显示分辨率，不是事件精度。

仅目标流约占满一个方向 23 μs；同向 EP 的两条流交替分享同一个 100 Gbps 方向，持续到
约 46 μs；反向 EP 同时占用 ``+100/-100 Gbps`` 两个全双工方向，目标流几乎不变；
KV case 在 target 完成后继续占用链路到 66 μs。

.. figure:: _static/round09/block24-ep-kv-microsecond-profile.svg
   :alt: EP和KV同向竞争的微秒级带宽队列与task完成事件
   :align: center
   :class: study-figure

   **图 6：同向 EP+KV 的五层微秒级事件显微镜。** 从上到下为精确逐包序列化、
   0.25-μs 带宽、核心端口队列、累计 wire bytes，以及从首包到数据完成和 last-ACK
   的 task 生命周期。

:数据来源: ns-3 ``AllPacketTrace``、``PortTrace``、``QueueTrace`` 和 ``task_statistics.csv``；汇总为 ``network_core_packet_events.csv`` 与三份 ``network_*_timeseries.csv``。
:物理量与单位: packet serialization（ns/μs）、data bandwidth（Gbps）、queue/cumulative wire bytes（KiB）、task/ACK 时间（μs）。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: 逐包调度和 queue 是仿真状态，不是硬件抓包或交换机 buffer counter；task window 不是 GPU kernel 时间。

目标流/KV 分别有 68/128 个 data packet；每个 4,174-B wire 满包在 100 Gbps core 上
占用 333.92 ns。两条流先逐包交替；目标最后一包在 45.194 μs 离开 core，KV 随后
单独排空到 65.603 μs。队列在 10.7805 μs 达到 668.6 KiB；目标/phase ACK 为
46.979/66.013 μs。累计 277.2/521.8 KiB wire bytes 与 272/512 KiB payload 的差额
来自 packet overhead。

.. figure:: _static/round09/destination-access-bandwidth-timeseries.svg
   :alt: 均衡Zipf热点目的端分布下四个access端口的微秒级带宽
   :align: center
   :class: study-figure

   **图 7：相同总 bytes 下，四个目的端口的带宽随时间变化。** 堆叠面积是 dst4--dst7，
   黑线是四个独立 100 Gbps access ports 的合计数据带宽。

:数据来源: 目的端 ``PortTrace`` 的精确 egress 时间和 packet/task mapping；``research/round09/destination_access_bandwidth_timeseries.csv``。
:物理量与单位: 横轴为时间（μs）；纵轴为 per-destination 和 aggregate data bandwidth（Gbps），0.25 μs 分箱。
:证据类型: ``ns3-simulated/trace-derived``。
:解释边界: 目的端份额是控制变量，不是实测 token histogram；总带宽是四端口之和，不是一条 400 Gbps 链路。

balanced/Zipf/hot 在 phase 内的平均合计带宽为 378.4/203.7/102.1 Gbps，即约
3.78/2.04/1.02 个并行目的端口。热点 case 并不是链路本身变慢，而是绝大多数流量只能
串行经过一个 100 Gbps 目的端口，其余端口很快空闲。

四、代表性中间时间切片
----------------------

Block 24 不是一个 batch 或请求，而是 **request 0 / batch 0 prefill 中，48 个 transformer
blocks 的零起编号 24（顺序第 25 个）中间 block**。原始行从 ``layernorm_289`` 开始，
含 ``expert_297/299``。它位于执行中部，同时包含 attention、TP AllReduce、
MoE dispatch、rank-local expert 和 combine，并且邻近 block 结构重复。

.. figure:: _static/round09/block24-gpu-nic-timeline.svg
   :alt: Block 24 GPU0 NIC0 GPU1 NIC1统一时间线
   :align: center
   :class: study-figure

   **图 8：GPU0/NIC0/GPU1/NIC1 的统一事件流。** 两个 expert compute 各 456.737 μs
   并行；NIC lane 显示 AllReduce、AllGather 和 ReduceScatter 的方向、payload 和 task 时间。

:数据来源: round-07 ``block24_unified_events.csv``，本轮复用同一语义切片作为竞争 probe。
:物理量与单位: 横轴为相对 block 起点的组合时间（μs）；条宽为 operation/task duration，payload 为 KiB。
:证据类型: GPU 为 ``profile-derived``，peer 为 ``algorithm-projected``，NIC 为 ``ns3-simulated``。
:解释边界: 时间线没有真实 GPU/NIC overlap，也不是 A100/NCCL profiler timeline。

原始 trace 的关键顺序为：

.. code-block:: text

   LayerNorm → QKV → QK norm → Rotary → Attention → O projection
   → AllReduce 524,288 B
   → LayerNorm
   → AllGather 278,528 B
   → expert_297 / expert_299, each 456.737 μs in parallel
   → ReduceScatter 524,288 B

此前无背景流闭环还能把 AllGather 的一条 278,528 B peer flow 下钻为 5 个 WQE segments、
68 个 4 KiB data packets、逐 hop delay、ACK、queue 和 port。本轮把相同语义 probe 放入
core 竞争、反向流、KV proxy 和热点目的端中，补上“网络竞争时会怎样”。

五、Trace 生成和最细粒度
------------------------

.. figure:: _static/round09/trace-granularity-ladder.svg
   :alt: request batch block collective peer flow WQE packet hop queue粒度阶梯
   :align: center
   :class: study-figure

   **图 9：服务语义到离散事件网络的粒度阶梯。** 当前最细为逐 packet、逐 hop、逐
   queue/port，但上游 peer flow 仍需显式算法投影。

:数据来源: ``research/round09/trace_lineage.csv``。
:物理量与单位: 数据层级、字段和关联键；方法图，无数值单位。
:证据类型: ``trace-derived + algorithm-projected + ns3-simulated``。
:解释边界: 粒度更细不等于更真实；packet 结果仍依赖 peer schedule 和拓扑假设。

.. code-block:: text

   model JSON + request + cluster config + hardware profile
     → router/scheduler batch + prefill chunk
     → trace_generator layer rows
     → Chakra per-rank .et
     → ASTRA ETFeeder + collective algorithm
     → phase + src/dst/bytes/dependency peer flow
     → ns-3 traffic.csv
     → UB task → WQE segment → 4 KiB packet → hop → queue/port

要得到真正可校准的 ns-3 输入，需要三层同时采集：framework 层保留 request/batch/block/
collective 语义；CCL runtime 层提供真实 algorithm/channel/peer/chunk/stream；NIC/fabric 层
提供 wire bytes、counter、packet capture 和 switch telemetry。本轮第一层已完成，第二层用
ring 投影，第三层只有 ns-3 输出而没有 measured artifact。

六、LLMServingSim 的 CCL 实现
-----------------------------

LLMServingSim 不执行真实 NCCL/HCCL 数据面：

1. ``trace_generator.py`` 在 ``o_proj`` 和 dense ``down_proj`` 后发出 TP AllReduce；
2. 默认 vLLM ``allgather_reducescatter`` backend 下，MoE dispatch 发出 AllGather，
   combine 发出 ReduceScatter；函数旧 docstring 虽写 AllToAll，本页以实际 trace 为准；
3. collective 编码为 Chakra ``COMM_COLL_NODE``，携带 ``comm_type、comm_size、
   involved_dim、dependency``；
4. ASTRA ``Workload::issue_comm()`` 调用对应 ``generate_*``；
5. 本轮 ``system.json`` 对 AR/AG/RS/A2A 均选 ``ring``，optimization 为
   ``localBWAware``。

因此它能回答逻辑 collective 和 analytical timing，不能回答真实 runtime 选择了几个
channel、怎样切 chunk、GPU kernel/NIC 如何 overlap。代码路径与边界见
``research/round09/ccl_implementation_ledger.csv``。

七、384 NPU 候选映射
--------------------

.. figure:: _static/round09/huawei-384-logical-mapping.svg
   :alt: 通过配置校验的384逻辑设备DP64 TP2 PP3及共享EP128映射
   :align: center
   :class: study-figure

   **图 10：配置校验通过的 384 设备候选，不是 384 卡运行结果。** ``DP64 × TP2 × PP3``
   共 384 个逻辑设备；每个 PP stage 的 ``DP64 × TP2`` 共享同一个 EP128 group。
   每个 stage 包含 16 个 transformer blocks，EP 不是额外相乘的设备维度。

:数据来源: ``config_builder.py`` 实际校验；``research/goal-audit-20261008/verify_evidence.py`` 和 ``research/round09/huawei_384_mapping.csv``。
:物理量与单位: DP/TP/PP/EP rank 数、device/block 数，单位为 count。
:证据类型: ``config-validator-only/design-hypothesis``。
:解释边界: 128 experts 保持不变；节点内、超节点内和超节点外带宽/时延仍为 TBD。

实际接入时不能只填一个 bandwidth，应分别校准设备/节点内、超节点内、超节点外三层；
同时固定 placement，让 TP、EP、DP 流明确映射到对应物理位置。本轮不使用 100/400 Gbps
控制参数冒充华为 384 超节点配置。

2026-10-08 修正：旧的 ``DP3 × EP128`` / ``DP3 × TP2 × EP64`` 只是独立维度草图，
不能直接作为当前前端配置；``ep_size`` 必须被 DP group size 整除，而且 TP 与 EP 共享
设备。旧草图按三实例 DP group 解释均被实际校验器拒绝。本次通过的候选引入 PP，
研究的问题也随之改变；仍未执行大规模 workload，没有 NPU Profile 或目标物理拓扑校准。

带宽应从 cluster config 设置：``link_bw`` 单位 GB/s、``link_latency`` 单位 ns，可填
标量或与 ASTRA dimensions 等长的列表。``network.yml`` 是生成结果，每次运行会被
重建；仅手改它不能稳定保存设置。逻辑维度不自动等于节点内/外位置，必须另外定义
rank placement 和 ns-3 每条物理链路。配置示例与校验见 :doc:`goal-evidence-audit`。

八、平台粒度与实验代表性
------------------------

.. list-table:: 平台分工
   :header-rows: 1
   :widths: 24 26 28 22

   * - 平台/层
     - 最细粒度
     - 覆盖问题
     - 主要缺口
   * - SimAI + ASTRA analytical
     - layer/collective/并行维度
     - 大规模 DP/TP/EP/PP 时间构成
     - packet queue 和真实 peer
   * - LLMServingSim
     - request→batch→block→operator/collective
     - TTFT、TPOT、调度、KV、模型 shape
     - CCL channel 和 packet queue
   * - Algorithm projection
     - phase→peer flow
     - 谁到谁、多少 bytes、依赖
     - runtime 是否选择该算法
   * - OpenUSim/ns-3-UB
     - task/WQE→packet→hop→queue/port
     - 共享瓶颈、FCT、queue、throughput
     - 未校准硬件的绝对精度

Block 24 的代表性在于“中间 MoE block + 完整通信阶段 + 可下钻粒度”，不是因为它代表
所有生产请求。11 个竞争 case 通过公平 control 分别隔离方向、竞争强度和目的端倾斜，
适合做机制研究；2-rank、确定性最短路、同时释放和无真实多 rail 限制了外推范围。

九、下一步校准闭环
------------------

下一步应在多节点 A100 或目标 NPU 环境复验相同消息规模、peer 数、方向和热点矩阵，同步
采集 framework/CCL/NIC 三层 trace；再用 measured FCT、P95/P99 和 counter 校准 ns-3，
最后把误差区间反馈到 LLMServingSim 的 TTFT/TPOT/SLO。这样形成的是
``仿真预测 → 真机测量 → 参数校准 → 再预测`` 的学术与工程闭环，而不是把仿真数值直接
写成硬件结论。

数据与报告
----------

可复核 Markdown 报告和 CSV 总账位于仓库 ``research/round09/``：

* ``report.md``：完整报告；
* ``llmservingsim_input_sweep.csv``：6 组请求与 trace 统计；
* ``network_contention_summary.csv``、``network_contention_tasks.csv``：11-case 汇总与逐 task；
* ``model_inventory.csv``、``hardware_profile_inventory.csv``：模型和硬件参数；
* ``trace_lineage.csv``、``ccl_implementation_ledger.csv``：trace 和 CCL 路径；
* ``huawei_384_mapping.csv``、``evidence_ledger.csv``：384 映射与证据总账。
