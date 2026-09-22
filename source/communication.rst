通信竞争与尾时延
================

“一次通信需求”不是一个孤立的固定延迟。它从操作语义开始，经分块、资源门控、路径和协议事件，
最终得到接收就绪和发送事务完成两个时间。其他传输在同一时间使用相同资源时，才形成竞争。

一次通信的生命周期
------------------

.. raw:: html

   <ol class="lifecycle" aria-label="一次传输的生命周期">
     <li><b>语义产生</b><span>某 wave、层、Dispatch/Combine/KV operation</span></li>
     <li><b>流量展开</b><span>src、dst、bytes、chunk、tag、transfer_id</span></li>
     <li><b>ASTRA 就绪</b><span>依赖满足且 SEND 槽可用</span></li>
     <li><b>UB 提交</b><span>创建 Jetty/TP/WQE，分配 ub_task_id</span></li>
     <li><b>网络竞争</b><span>分段、分包、端口队列、共享链路、RTP、ACK</span></li>
     <li><b>双完成</b><span>data_ready 解锁接收计算；transaction_done 释放发送槽</span></li>
   </ol>

竞争发生在哪里
--------------

.. list-table:: 从端侧到网络的竞争资源
   :header-rows: 1
   :widths: 20 29 25 26

   * - 位置
     - 竞争变量
     - 直接结果
     - 可观测证据
   * - 服务调度
     - arrival、batch 上限、KV 容量、实例 busy
     - 提交时刻与 batch 组成
     - protocol.jsonl、service metrics
   * - 执行图
     - COMP/SEND/RECV 依赖、层序
     - 哪些流能同时就绪
     - semantics.json、endpoint events
   * - 发送端
     - send slots、chunk 数、WQE/Jetty/TP
     - 排队下发、head-of-line
     - send_submit、transaction_done
   * - 入口/出口端口
     - 同时到达的包、端口速率、队列和优先级
     - serialization、queueing、丢包/反压
     - port_windows、packet trace
   * - 共享链路
     - 路径重叠、方向、incast/fan-out
     - 带宽分摊与热点
     - 每端口字节和 transfer 归属
   * - 接收端
     - 多源 incast、segment 重组、目标处理
     - ``data_ready`` 尾部
     - recv/data_ready 事件
   * - 可靠传输
     - ACK/SACK、窗口、重传/控制包
     - ``transaction_done`` 尾部
     - control bytes、transaction_done

只有路径和时间窗口同时重叠的流才会竞争。全双工链路上反方向流量通常不会争用同一个发送方向，
因此“跨过同一对交换机”不等于“共享同一瓶颈队列”。

两种完成时间
------------

接收依赖使用：

.. code-block:: text

   T_ready = recv_done_ts - submit_ts

它表示目标端累计收到足够字节，可以消费数据并启动后继计算。

发送资源使用：

.. code-block:: text

   T_txn = transaction_done_ts - submit_ts

它表示源端完成事务 ACK 链，可以释放 SEND 槽。通常 ``T_txn >= T_ready``，但不应靠该关系反推
接收完成；两者来自不同事件源。

尾时延变量
----------

单条 transfer 的尾时延可按因果阶段理解，而不是简单相加为一个不可解释常数：

.. code-block:: text

   T_transfer =
       T_wait_dependency
     + T_wait_send_slot
     + T_endpoint_submit
     + T_serialization
     + T_switch_queue
     + T_flow_control
     + T_propagation
     + T_receiver_reassembly
     + T_ack_tail

其中最容易驱动 P95/P99 的变量包括：

* 同一目的端的并发发送者数量，即 incast fan-in；
* 每个 ``(src,dst)`` 的字节数和 chunk 数；
* 路由倾斜与专家放置共同形成的热点位置；
* SEND 槽数及其释放语义；
* 端口服务速率、缓冲、优先级与流控阈值；
* 路径是否共享，是否同方向重叠；
* 背景流量、P/D KV 与 EP Dispatch/Combine 的时窗重叠；
* 接收端最后一个 segment 与源端最后一个 ACK；
* 计算时长，因为它决定流何时进入网络并形成同步突发；
* batch 组成，因为它同时改变计算量、通信量和同步关系。

服务级尾时延还包含：

.. code-block:: text

   request latency = queue wait
                   + Σ compute critical path
                   + Σ communication critical path
                   + P/D handoff wait
                   + KV capacity wait
                   + later-wave feedback

网络竞争不仅延长当前传输，还会改变下一次调度时刻和 batch 组成，因此尾部影响可以跨 wave 传播。

如何判断竞争而不是相关
----------------------

至少需要三个条件：

1. **资源重叠**：两组 transfer 经过同一方向端口或共享资源；
2. **时间重叠**：其在途区间或端口窗口相交；
3. **受控对照**：保持请求、路由、计算和网络参数不变，只移除竞争流或改变瓶颈。

推荐对照：

.. list-table:: 竞争实验设计
   :header-rows: 1
   :widths: 20 25 28 27

   * - 对照
     - 唯一改变量
     - 主要指标
     - 证伪信号
   * - 独跑 vs 同跑
     - 是否存在另一实例
     - transfer P50/P95/P99、wave 完成
     - 同路径同方向重叠但时延完全不变
   * - 路由均匀 vs 倾斜
     - routes.jsonl
     - rank-pair matrix、热点端口、专家 straggler
     - 流量矩阵未改变
   * - send slots 扫描
     - ``max_send_slots``
     - 注入并发、队列峰值、transaction tail
     - 实际在途数未变化
   * - chunk 扫描
     - ``chunk_bytes``
     - task 数、控制开销、完成顺序
     - bytes 守恒失败或模板改变
   * - P/D 与 EP 错峰
     - KV 提交时刻/策略
     - 共享端口窗口、请求尾延迟
     - 路径或方向并不共享
   * - 带宽/队列扫描
     - 单一网络参数
     - 排队、端口利用率、tail slope
     - 计算路径同时改变

当前已有竞争证据
----------------

``T07`` 已验证两个实例共享瓶颈会互相干扰：同一仿真内同跑相对独跑，Dispatch 最大耗时
由 1,642 ns 增至 2,978 ns；A 的下一 wave 提交由 22,207 ns 推迟到 24,808 ns。
这同时证明了共享网络竞争和“网络完成→下一轮调度”的反馈。

``T10.4`` 已验证 P/D KV 和背景 EP 流量在同向共享瓶颈重叠时，KV transfer 比独跑更慢。
早期错误 fixture 将流量放在瓶颈同侧，测试没有观察到变慢，从而暴露了“拓扑跨越与方向”必须明确验证。

尾时延分析最低数据集
--------------------

* ``truth/transfer_registry.jsonl``：每 transfer 的 submit、recv_done、transaction_done、bytes；
* ``truth/port_attribution_windows.csv``：transfer 在哪些端口/窗口贡献了多少字节；
* ``truth/operation_registry.jsonl``：operation 的请求、wave、层和全局边界；
* ``logs/protocol.jsonl``：wave 完成如何触发下一次提交；
* ``metrics/service_metrics.json``：网络尾部最终落到哪些请求；
* ``observed/``：仅用于模拟真实算法输入，不能混入上述真值做在线判定。

