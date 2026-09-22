三层系统架构与增减量
====================

先区分三套系统各自拥有的状态，才能正确理解“复用”和“提升”。

总体关系
--------

.. raw:: html

   <div class="pipeline" aria-label="从用户输入到网络观测的数据流">
     <div class="pipeline-node"><b>用户输入</b><span>请求、模型、路由、放置、Profile、网络</span></div>
     <div class="pipeline-arrow">→</div>
     <div class="pipeline-node"><b>服务前端</b><span>接纳、组批、KV、P/D、wave</span></div>
     <div class="pipeline-arrow">→</div>
     <div class="pipeline-node"><b>语义 Lowerer</b><span>Operation、Transfer、COMP/SEND/RECV</span></div>
     <div class="pipeline-arrow">→</div>
     <div class="pipeline-node"><b>ASTRA-sim</b><span>依赖和执行资源</span></div>
     <div class="pipeline-arrow">→</div>
     <div class="pipeline-node"><b>ns-3-UB</b><span>WQE、分段、分包、排队、流控、ACK</span></div>
     <div class="pipeline-arrow">→</div>
     <div class="pipeline-node"><b>观测与真值</b><span>端侧操作、端口窗口、归属</span></div>
   </div>

ASTRA-sim：通用执行层
----------------------

ASTRA-sim 接收已经构造好的执行图。图节点描述计算、集合通信或点对点通信，边描述依赖；
System 和 HardwareResource 决定节点何时可以执行，网络后端决定通信完成时间。

它拥有：

* Chakra/ET 图读取；
* COMP、COMM_COLL、COMM_SEND、COMM_RECV 等节点；
* 计算资源和通信资源占用；
* Collective 算法与网络后端接口；
* 计算/通信完成回调。

它没有天然拥有：请求到达、prefill/decode、KV block、连续组批、TTFT/TPOT、Token→专家语义。
这些必须由上层生成图时提供。

LLMServingSim：ASTRA-sim 之上的服务前端
---------------------------------------

LLMServingSim 增加了：

* 请求、Batch、Router 和类 vLLM continuous batching；
* chunked prefill、KV 容量、抢占、prefix cache 与分层内存；
* Transformer 层序、TP/PP/EP/DP、P/D 分离、MoE GateRouter；
* vLLM layerwise profiler 和按形状插值；
* trace generator、Chakra graph generator 和 ASTRA 子进程控制器；
* TTFT、TPOT、ITL、吞吐、内存、功耗等服务指标；
* bench 与真实 vLLM 的对照验证。

代价与抽象损失是：MoE 网络通信主要仍以一个 ``comm_size`` 的 ALLTOALL 表示。
GateRouter 的倾斜可以改变各 EP rank 的 ``local_tokens`` 和专家计算时长，但不能完整保留
每个 ``(src_rank, dst_rank)`` 的非均匀字节矩阵。

联合框架：增加语义通信与 UB 数据面
-----------------------------------

联合框架直接复用：

* 固定版本 ASTRA-sim 的 Workload、ETFeeder、Sys、HardwareResource 与网络 API；
* Chakra schema 和 COMP/SEND/RECV 图；
* ns-3-UB 的拓扑、路由、交换机、端口队列、Jetty、WQE、RTP、流控与 ACK；
* LLMServingSim 的锁定基线、模型/Profile 资料和配置思想。

联合框架新增或修改：

* 新写的轻量 ServiceFrontend，而不是复用 LLMServingSim Scheduler 类；
* RouteProvider、Placement、ExecutionPlan 和 CommunicationLowerer；
* ``direct_rank_dedup_v1``：逐 Token 路由到逐 rank-pair 传输；
* ASTRA ``load_workload()/activate()`` 两阶段装载；
* ASTRA 多 SEND 槽与 ``pending_recv_count`` 精确图完成；
* AstraNetworkAPI→URMA_WRITE/RTP 的 UbNetworkAdapter；
* 动态 UbTransferExecutor，不依赖预先写死的 ``traffic.csv``；
* ``data_ready`` 与 ``transaction_done`` 两套完成语义；
* 以 ns-3 为唯一绝对时钟的 ``DECIDE/COMMIT`` 服务闭环；
* 端口窗口、设备本地时钟、操作—传输—任务—端口归属真值。

增量与减量
----------

.. list-table:: 相对关系不是单向“更强”，而是研究目标不同
   :header-rows: 1
   :widths: 19 27 27 27

   * - 维度
     - ASTRA-sim
     - LLMServingSim 相对增量
     - 联合框架相对增/减量
   * - 服务语义
     - 无请求调度
     - 增加完整请求/KV/批处理状态
     - 保留必要状态，但调度器简化为 FIFO/wave
   * - 模型广度
     - 通用图
     - TP/PP/EP/DP、CXL/PIM、prefix、功耗
     - 当前聚焦 EP+P/D，减去多数通用特性
   * - 通信粒度
     - Collective 或点对点图节点
     - 主要是标量 Collective
     - 增加逐 rank-pair、chunk、UB task、segment/packet
   * - 网络反馈
     - 由所选后端决定
     - 每轮返回 cycle 给前端
     - 持久共享 fabric，绝对事件时间反馈下一轮调度
   * - 观测
     - 通用执行统计
     - 服务指标
     - 增加端口窗口、归属真值、时钟隔离
   * - 可扩展性
     - analytical 路径较强
     - EP=320 已跑通但内存较高
     - 包级高保真，当前大规模上限尚未实测

职责边界
--------

.. list-table:: 每类状态只有一个权威来源
   :header-rows: 1
   :widths: 34 32 34

   * - 状态
     - 权威组件
     - 禁止重复建模
   * - 请求、队列、phase、KV 容量
     - ServiceFrontend
     - UB 不再调度请求依赖
   * - Token→专家、专家→rank、业务字节
     - RouteProvider / Placement / Lowerer
     - ASTRA 不再二次展开同一 ALLTOALL
   * - 本地图依赖、计算资源、SEND 槽
     - ASTRA Workload / HardwareResource
     - UB 任务 delay 不重复加入计算耗时
   * - 分段、分包、路由、队列、流控、ACK
     - ns-3-UB
     - 前端只声明业务 transfer
   * - 绝对时间
     - ns3::Simulator
     - Python 不执行 ``current += duration``
   * - 归属真值
     - Observer + exporter 侧表
     - 不用网侧时间反推端侧操作边界
