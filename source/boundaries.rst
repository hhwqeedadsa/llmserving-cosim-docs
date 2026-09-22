能力边界、限制、上限与粒度
==========================

粒度总览
--------

.. list-table:: 三套系统能看见的最细对象
   :header-rows: 1
   :widths: 20 25 27 28

   * - 维度
     - ASTRA-sim
     - LLMServingSim
     - MoE-UB 联合框架
   * - 服务
     - 无原生请求
     - request/session、batch、iteration
     - request、phase、wave、handoff
   * - 模型
     - 图节点
     - layer/operator、attention/MoE
     - layer、operation、rank 本地计算
   * - MoE 路由
     - 由输入图决定
     - token→expert 后聚合为 rank load
     - request/token/layer→expert→rank-pair
   * - 通信
     - Collective 或 SEND/RECV
     - 标量 ``comm_size`` 的 Collective 为主
     - operation→transfer→chunk→UB task
   * - 网络
     - 取决于 backend
     - 主路径为 analytical collective
     - WQE→segment→packet→switch port/window
   * - 时间
     - 执行引擎 tick/cycle
     - batch/iteration 返回周期
     - ns-3 全局 ns + 设备本地时钟
   * - 指标
     - 执行/通信统计
     - TTFT、TPOT、ITL、吞吐、内存、功耗
     - 请求延迟、传输完成、端口归属与守恒

ASTRA-sim 的边界
----------------

适合：

* 已知执行图的计算—通信依赖研究；
* TP/PP/Collective 算法与拓扑探索；
* analytical 后端的大规模快速比较；
* 作为不同前端共同的执行内核。

限制：

* 不替用户定义请求、批处理、KV 或 Token 路由；
* 图里的通信需求如果已经被过度聚合，后端无法恢复真实 ``src→dst`` 矩阵；
* analytical 模型通常不包含真实通信 kernel 启动、GPU/CPU proxy、特定运行时 channel/FIFO；
* 精度上限由输入图、collective 分解和网络参数共同决定，而不只是 ASTRA 本身。

LLMServingSim 的边界
--------------------

适合：

* LLM Serving 调度、KV、并行策略和硬件配置的端到端 what-if；
* vLLM 风格 continuous batching、chunked prefill、prefix cache；
* TP/PP/EP/DP 组合，P/D、CXL、PIM、功耗；
* 由真机 layerwise Profile 驱动的请求延迟和吞吐预测。

关键限制：

* MoE ALLTOALL 使用整体 ``comm_size``，不能完整表达 per-pair 倾斜和特定热点路径；
* ``block_copy`` 对 RAND/CUSTOM 路由可能近似重复首层行为；
* CUSTOM GateRouter 是代码入口，不等同于已接入真实逐 Token trace 的稳定数据接口；
* Profile 插值范围、vLLM 版本与 profiler hook 决定计算精度；
* KV 容量需与真实 vLLM block 数校准，否则抢占和尾延迟会显著偏移；
* analytical 网络不能回答 UB/RDMA 事务、包队列、ACK 和端口窗口问题。

已实测规模：本地 EP=320 配置在 analytical 后端跑通。10 请求、48 层模型、约 591 个生成 token，
墙钟约 1,346 秒，峰值 RSS 约 30,364 MB。模型专家数和 Profile 使用了 320 专家规模测试垫片，
链路参数也是占位值，因此该实验只证明工程可运行性和资源成本，不证明性能精度。

联合框架的边界
--------------

当前已支持：

* EP Dispatch/Combine 的逐 Token 路由与非均匀 rank-pair 传输；
* 多层 dense/MoE 图和固定专家放置；
* P/D 三阶段状态机与 KV 通过 UB 网络交接；
* 多实例共享同一持久 UB fabric；
* URMA_WRITE/RTP、发送槽、data_ready/transaction_done；
* 端口时窗、本地时钟、business/background/control 和独立归属真值；
* T01–T14、守恒、观测隔离和可复现性测试。

当前明确不支持或受限：

.. list-table:: 联合框架限制
   :header-rows: 1
   :widths: 22 36 42

   * - 范围
     - 当前边界
     - 影响
   * - 调度
     - 新写的 FIFO/wave 状态机，不是 LLMServingSim/vLLM Scheduler
     - 不适合研究复杂调度策略的绝对收益
   * - 并行
     - 当前交付集中 EP + P/D；TP/PP 联合图未实现
     - 不能覆盖完整生产并行组合
   * - 传输
     - 只支持 URMA_WRITE/RTP；priority 固定路径有限，多径未系统接入
     - 不能泛化到 READ/LDST/CTP 或其他 QoS 行为
   * - 通信模板
     - ``direct_rank_dedup_v1`` 是参考模型
     - 不等价于 DeepEP/HCCL/NCCL 的 channel、同步和完成语义
   * - P/D 策略
     - ``between_iterations``、单请求 FIFO，交接期间相关实例暂停
     - 不能表达异步/合并 KV 和计算通信重叠
   * - 执行资源
     - 一 rank 对应一 NPU、每 rank 同时一张图
     - 不支持单 NPU 多模型抢占和复杂微批交叠
   * - 路由
     - fixture 为合成路由；真实 trace 接口已具备
     - 尚不能声称真实模型热点分布
   * - 计算
     - fixture/参考 Profile，部分标记 ``unvalidated_reference``
     - 不能承诺目标 NPU 的绝对时延
   * - 网络校准
     - 尚缺小规模真机或硬件在环对照
     - 工程正确不等于预测准确
   * - 规模
     - 当前主要验证 4–6 rank；EP=320 包级基准未完成
     - 长时间大规模仿真可能受事件量、图文件和内存限制
   * - 分布式
     - 单进程 ns-3，未做跨机并行仿真
     - 上限受单机 CPU/内存约束

粒度的增益与代价
----------------

.. list-table:: 选择最小足够保真度
   :header-rows: 1
   :widths: 19 24 27 30

   * - 保真度
     - 事件对象
     - 能回答
     - 代价/盲区
   * - Analytical
     - operation/collective
     - 大规模趋势、并行策略、带宽敏感性
     - 看不到包队列与协议尾部
   * - Flow-level
     - src-dst flow/chunk
     - 非均匀矩阵、路径竞争、近似 tail
     - 需要校准排队/ACK 参数
   * - Packet-level
     - segment/packet/port event
     - incast、流控、控制包、端口归属
     - 事件量和内存随规模/时长急剧增长

推荐未来采用多保真：analytical 做 EP=16–320 扫描，flow-level 保留非均匀矩阵和完成接口，
packet-level 只重放热点窗口和代表场景。

哪些结论现在可以说
------------------

可以：

* 当前参考模板下字节矩阵、图依赖、发送/接收匹配和完成语义正确；
* 多实例与 KV/EP 流量可以在同一网络中竞争，并反馈调度；
* 端口计数和归属满足逐窗口守恒；
* observed/truth 隔离和固定输入可复现。

暂时不能：

* 某真实模型在目标 UB 超节点上的精确 P99；
* ``direct_rank_dedup_v1`` 等价于某实际通信运行时；
* 合成路由得到的热点代表真实模型；
* 4–6 rank 的性能和仿真速度可直接外推 EP=320；
* 包级联合框架整体优于 LLMServingSim——二者优化目标和能力广度不同。

