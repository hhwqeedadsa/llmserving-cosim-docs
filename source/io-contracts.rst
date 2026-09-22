组件输入输出契约
================

本页按实际数据流列出每个组件吃什么、吐什么，以及输出交给谁。用户通常只准备静态输入；
ExecutionPlan、Chakra 图、DECIDE/COMMIT 和 UB task 都由框架生成。

ASTRA-sim 组件
--------------

.. list-table:: ASTRA-sim 核心组件 I/O
   :header-rows: 1
   :widths: 20 28 28 24

   * - 组件
     - 输入
     - 输出
     - 消费者
   * - ETFeeder
     - 每 rank Chakra ``.et`` 图
     - 可下发节点与依赖就绪状态
     - Workload
   * - Workload
     - 图节点、完成回调
     - ``sim_schedule/send/recv``、rank finished
     - HardwareResource、NetworkAPI、Driver
   * - HardwareResource
     - 节点类型、计算/通信资源、SEND 槽数
     - acquire/release 决策
     - Workload
   * - Sys
     - COMP runtime、通信调用
     - 计算完成或通信完成事件
     - Workload
   * - AstraNetworkAPI
     - ``src,dst,count,type,tag``、相对延迟
     - SEND/RECV/定时完成回调、当前时间
     - ASTRA 执行层
   * - 原生 network backend
     - topology/system 参数、Collective
     - 通信完成时间
     - Workload

ASTRA 的输入已经是“如何执行”的图，而不是“要服务哪些请求”的业务描述。

LLMServingSim 组件
------------------

.. list-table:: LLMServingSim 前端与工具链 I/O
   :header-rows: 1
   :widths: 20 31 29 20

   * - 组件
     - 输入
     - 输出
     - 下游
   * - Workload loader
     - ``input_toks``、``output_toks``、``arrival_time_ns``、可选 token IDs/agentic session
     - Request 对象
     - Router
   * - Router
     - 新到达请求、实例负载、路由策略
     - 实例选择和 waiting queue
     - Scheduler
   * - Scheduler
     - waiting/running、token budget、KV block、prefix 命中、时间
     - Batch、抢占/恢复、请求状态更新
     - TraceGenerator
   * - MemoryModel / KV manager
     - 模型形状、容量、block size、tier 配置
     - 权重/KV 占用、分配/逐出/召回
     - Scheduler、TraceGenerator
   * - GateRouter
     - token 数、Top-K、策略、EP 度
     - ``local_tokens``、``activated_experts``
     - MoE compute lookup
   * - Profiler
     - 真实 vLLM、硬件、模型、shape sweep
     - dense/attention/moe/skew CSV 与 meta.yaml
     - TraceGenerator
   * - TraceGenerator
     - Batch、层序、Profile、并行配置
     - 每层计算与 Collective trace rows
     - GraphGenerator
   * - GraphGenerator
     - trace rows
     - Chakra ``.et``
     - ASTRA-sim
   * - ConfigBuilder
     - cluster JSON
     - ASTRA network/system/memory 输入
     - ASTRA-sim
   * - Controller
     - 图路径、``pass/done``
     - ``Waiting``、cycle count
     - 主循环
   * - Bench
     - 相同 workload 与 vLLM 配置
     - vLLM 真值、sim.csv、误差图表
     - 验证报告

联合框架组件
------------

.. list-table:: MoE-UB 联合框架 I/O
   :header-rows: 1
   :widths: 20 30 30 20

   * - 组件
     - 输入
     - 输出
     - 下游
   * - CLI
     - service 配置、run-dir、backend 覆盖
     - 后端进程、标准运行目录
     - 用户/CI
   * - ServiceFrontend
     - 静态输入、``DECIDE``
     - ``COMMIT``、图文件、next wakeup
     - CoSimDriver
   * - RequestState
     - 一条请求记录
     - phase、step、KV 预留、finished
     - ServiceFrontend
   * - Model
     - model.json
     - 层序、专家、dtype、KV 字节
     - Lowerer、前端
   * - TraceRouteProvider
     - ``(request,phase,token,layer)``
     - expert ID 列表
     - Lowerer
   * - Placement
     - expert→rank、rank→UB node
     - 目标 rank
     - Lowerer
   * - ComputeProvider
     - op/phase/layer/tokens/device/dtype
     - ``duration_ns``
     - Lowerer
   * - CommunicationLowerer
     - batch、路由、放置、计算时长、模板
     - ExecutionPlan
     - ChakraExporter
   * - ExecutionPlan
     - Operation、Transfer、COMP/SEND/RECV
     - 已校验 IR
     - Exporter
   * - ChakraExporter
     - ExecutionPlan
     - ``.et``、transfers.csv、semantics.json
     - ASTRA、Adapter、RunExporter
   * - CoSimDriver
     - COMMIT、图、next wakeup
     - DECIDE、图原子激活、死锁/结束判定
     - 前端、ASTRA
   * - UbNetworkAdapter
     - ASTRA 网络调用、manifest、UB 完成事件
     - UB task、ASTRA 回调、端点日志
     - Executor、ASTRA、Exporter
   * - UbTransferExecutor
     - dst、bytes、priority
     - Jetty/WQE/taskId、transaction_done
     - ns-3-UB、Adapter
   * - ns-3-UB
     - network case、运行时 WQE
     - data_ready、transaction_done、packet events
     - Adapter、Observer
   * - UbPortObserver
     - packet event、clock、task→transfer
     - port windows、分类、归属
     - RunExporter
   * - RunExporter
     - 配置、日志、语义、观测
     - manifest/inputs/observed/truth/metrics
     - 算法与评价程序

关键协议边界
------------

``cosim/1`` 的交互单位是服务决策时刻，而不是包：

.. code-block:: json

   {"schema_version":"cosim/1", "type":"DECIDE",
    "decision_id":17, "now_ns":24808,
    "completed":[{"kind":"WAVE_DONE", "wave_id":"A.w0"}],
    "available_ranks":[0,2]}

.. code-block:: json

   {"schema_version":"cosim/1", "type":"COMMIT",
    "decision_id":17,
    "submissions":[{"kind":"ep_wave", "graph_id":"A.w1", "rank_ids":[0,2]}],
    "next_wakeup_ns":null, "service_finished":false}

图文件承载大量节点和 transfer，JSONL 只承载控制消息，从而避免每个包都唤醒 Python。

标准运行目录
------------

.. code-block:: text

   runs/<run_id>/
     manifest.json
     inputs/                 # 原始输入快照与 hash
     graphs/                 # .et / transfers.csv / semantics.json
     logs/                   # endpoint_events / protocol / stdout
     observed/               # 算法可读，本地时钟、无全局真值
     truth/                  # 操作、传输、端口归属、时钟模型
     metrics/                # 服务完成与延迟

