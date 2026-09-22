观测数据、真值与中间产物
========================

框架输出分为四个安全域。可视化前必须先决定面向的是调试者、研究算法还是评价程序；
不同角色不能混用字段。

四类输出
--------

.. list-table:: 数据域与允许用途
   :header-rows: 1
   :widths: 16 30 28 26

   * - 目录
     - 典型文件
     - 用途
     - 约束
   * - ``graphs/``
     - ``.et``、transfers.csv、semantics.json
     - 检查图、依赖、传输展开与来源
     - 是生成期中间产物，不等于运行结果
   * - ``logs/``
     - endpoint_events、protocol、stdout
     - 调试回调、状态机与协议
     - 可含全局时间和内部标识
   * - ``observed/``
     - endpoint_ops、port_windows、public_config
     - 端网关联算法唯一允许读取的输入
     - 只给本地时钟和聚合计数，不给归属真值
   * - ``truth/``
     - operation/transfer registry、attribution、clock_models
     - 离线评价、守恒和误差分析
     - 算法运行时禁止读取
   * - ``metrics/``
     - service_metrics.json
     - 请求完成、延迟、吞吐
     - 当前指标集较小，不含完整 TTFT/TPOT 分解

从请求到端口字节的可追溯链
--------------------------

.. raw:: html

   <div class="lineage" aria-label="请求到交换机端口的标识链">
     <span>request / token / layer</span><i>→</i>
     <span>operation_id</span><i>→</i>
     <span>transfer_id</span><i>→</i>
     <span>comm_tag</span><i>→</i>
     <span>ub_task_id</span><i>→</i>
     <span>packet + UbFlowTag</span><i>→</i>
     <span>switch / port / window</span>
   </div>

``semantics.json`` 保存 operation 与请求/层/实例的关系；``transfers.csv`` 保存 tag、src、dst、bytes；
Adapter 在提交时建立 ``ub_task_id→transfer_id``；UbFlowTag 随包经过交换机；Observer 在固定 hook
将包归属到端口窗口。内部 ID 不参与路径、QoS、流控或时延计算。

observed 数据字典
-----------------

``endpoint_ops.jsonl`` 每行是 rank 本地看到的一次逻辑操作：

.. list-table:: endpoint operation
   :header-rows: 1
   :widths: 28 22 50

   * - 字段
     - 单位/类型
     - 含义
   * - operation_id
     - string
     - wave、层、dispatch/combine/KV 的稳定标识
   * - kind
     - enum
     - ``dispatch``、``combine``、``kv_transfer``
   * - wave_id / layer_id / rank
     - ID
     - 本地执行上下文
   * - local_start_ns
     - 本地 ns
     - 端侧操作开始，不可直接与其他设备相减
   * - local_complete_ns
     - 本地 ns
     - 端侧操作完成

``port_windows.csv`` 每行是一个端口的一个本地时窗：

.. list-table:: port window
   :header-rows: 1
   :widths: 28 22 50

   * - 字段
     - 单位/类型
     - 含义
   * - switch_id / port_id / direction
     - ID
     - 观测位置，当前 direction 为 tx
   * - window_index
     - integer
     - 本设备时钟上的桶编号
   * - start_local_ns / end_local_ns
     - 本地 ns
     - 半开区间 ``[start,end)``
   * - packet_count
     - packets
     - 固定 hook 处计数的整包数量
   * - counted_packet_bytes
     - wire bytes
     - 包含协议头的线速字节

算法可以尝试把 endpoint operation 与 port windows 关联，但不能访问全局时间、clock model、
transfer 路径或已知归属。

truth 数据字典
--------------

``operation_registry.jsonl``
   请求、instance、phase、wave、layer、rank，以及全局 ``start_ns/complete_ns``。

``transfer_registry.jsonl``
   transfer、operation、src/dst、bytes、tag、taskId、submit、recv_done、transaction_done。

``attribution_windows.csv``
   operation 级归属窗口，便于评价端网关联结果。

``port_attribution_windows.csv``
   transfer/背景/控制在每个端口窗口的精确线速字节和业务 payload。

``clock_models.json``
   offset 和 drift；只用于把 observed 本地时钟映射回评价真值。

计数口径
--------

当前固定 hook 为 ``egress_packet_start``：包在交换机出口端口开始发送时，按整包计一次。
分类规则：

* 已登记 taskId 的 WRITE 数据包为 ``business``；
* ACK/SACK/CNP、WRITE 完成回执等为 ``control``；
* 有流标签但 taskId 未登记等其他流量为 ``background``；
* control 计入端口总量，但不归属业务 operation。

必须同时区分 ``counted_packet_bytes`` 与 ``business_payload_bytes``。前者包含线速协议开销，
后者是模型通信模板声明的业务数据，二者不应被强行设为相等。

观测隔离
--------

观测开关和时钟模型必须是只读旁路：

* 开关 Observer 不得改变业务事件序列、路由和完成结果；
* 改 offset/drift 只能改变本地窗口边界，不能改变全局执行；
* observed 不得包含 global timestamp、clock parameters、transfer attribution；
* 相同 run_id 重跑，logs/observed/truth/metrics 应逐字节一致；
* 仅路径字符串变化可能触发同刻事件的 ±1–2 ns 环境敏感性，因此 T13 按数据量、路径、
  完成结果和有界时间抖动核验，而不是跨路径逐字节比较。

守恒检查
--------

运行结果至少验证：

.. code-block:: text

   对每个 transfer：
     SEND bytes = RECV expected bytes = Σ目标端 data_ready segment bytes

   对每个端口窗口：
     counted_packet_bytes
       = Σ business attributed bytes
       + Σ background bytes
       + Σ control bytes

   对每个 operation：
     start <= complete
     所属 transfer、请求、wave、layer 可闭合

这些守恒比单独比较总字节更强，因为总量相同仍可能掩盖错端口、错窗口或错归属。

