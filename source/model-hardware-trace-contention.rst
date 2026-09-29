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
     - ``network.yml``
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

四、代表性中间时间切片
----------------------

Block 24 不是一个 batch 或请求，而是 **request 0 / batch 0 prefill 中，48 个 transformer
blocks 的第 24 个中间 block**。它位于执行中部，同时包含 attention、TP AllReduce、
MoE dispatch、rank-local expert 和 combine，并且邻近 block 结构重复。

.. figure:: _static/round09/block24-gpu-nic-timeline.svg
   :alt: Block 24 GPU0 NIC0 GPU1 NIC1统一时间线
   :align: center
   :class: study-figure

   **图 5：GPU0/NIC0/GPU1/NIC1 的统一事件流。** 两个 expert compute 各 456.737 μs
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

   **图 6：服务语义到离散事件网络的粒度阶梯。** 当前最细为逐 packet、逐 hop、逐
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
   :alt: 384 NPU DP TP EP候选映射及待校准带宽
   :align: center
   :class: study-figure

   **图 7：384 个逻辑 NPU ranks 的两个实验入口。** ``DP3 × EP128`` 让每个 EP rank
   管一个 expert；``DP3 × TP2 × EP64`` 让每个 EP rank 管两个 experts，并增加 TP 通信。

:数据来源: ``research/round09/huawei_384_mapping.csv``。
:物理量与单位: DP/TP/EP rank 数、expert 数，单位为 count。
:证据类型: ``design-hypothesis``。
:解释边界: 128 experts 保持不变；节点内、超节点内和超节点外带宽/时延仍为 TBD。

实际接入时不能只填一个 bandwidth，应分别校准设备/节点内、超节点内、超节点外三层；
同时固定 placement，让 TP、EP、DP 流明确映射到对应物理位置。本轮不使用 100/400 Gbps
控制参数冒充华为 384 超节点配置。

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
