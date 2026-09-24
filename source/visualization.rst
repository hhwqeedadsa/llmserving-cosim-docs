可视化方案与运行示例
====================

可视化应服务于一个明确问题。不要把所有字段堆进同一张 dashboard；先从“服务是否异常”、
“哪条传输形成尾部”、“在哪个端口竞争”、“属于哪个操作”逐层下钻。

完整的 SimAI / LLMServingSim / SimCCL / ns-3 静态实验图、逐图数据来源、物理量、单位和
证据边界见 :doc:`simai-llmservingsim-study`。本页保留运行目录的交互下钻方法，两者用途不同：
静态实验图用于报告和复核，交互浏览器用于探索单次联合框架运行。

实际运行数据浏览器
------------------

下面的交互图来自标准运行目录 ``runs/m5_t12`` 的压缩快照。该 fixture 使用合成路由和参考 Profile，
图用于展示数据链路与分析方法，不用于真实硬件性能结论。

.. raw:: html

   <div id="run-explorer" class="run-explorer">
     <div class="explorer-summary" aria-live="polite"></div>
     <div class="explorer-controls">
       <label>端口 <select id="port-select"></select></label>
       <label>传输类型 <select id="kind-select">
         <option value="all">全部</option>
         <option value="dispatch">Dispatch</option>
         <option value="combine">Combine</option>
         <option value="kv_transfer">KV transfer</option>
       </select></label>
     </div>
     <section>
       <h3>端口窗口线速字节</h3>
       <div id="port-chart" class="chart-host" role="img" aria-label="所选端口随时间变化的窗口字节"></div>
     </section>
     <section>
       <h3>Transfer 完成时延分布</h3>
       <div id="latency-chart" class="chart-host" role="img" aria-label="所选传输类型的时延直方图"></div>
     </section>
     <section>
       <h3>最慢 Transfer</h3>
       <div id="slow-table" class="table-scroll"></div>
     </section>
     <noscript>需要启用 JavaScript 才能查看交互图。</noscript>
   </div>

推荐视图
--------

.. list-table:: 问题到图形的映射
   :header-rows: 1
   :widths: 27 24 27 22

   * - 问题
     - 最合适的图
     - 数据字段
     - 判断信号
   * - 请求为什么慢
     - 请求 Gantt / critical path
     - arrival、wave、operation、finished
     - queue、compute、comm、handoff 哪段占尾部
   * - 哪条通信形成尾部
     - transfer latency ECDF/直方图 + top-N
     - submit/recv_done/transaction_done
     - P95/P99、ready 与 ACK tail 分离
   * - 哪个端口拥塞
     - 端口×时间热力图
     - port_windows
     - 同一时窗热点和持续时间
   * - 谁在该端口竞争
     - 堆叠时序或 Sankey
     - port attribution、operation/transfer
     - EP、KV、背景、控制的组成
   * - 路由是否倾斜
     - src×dst 流量矩阵、expert/rank load 条形图
     - transfers、routes、placement
     - 热行、热列、热点目标 rank
   * - 发送槽是否限制
     - rank lane timeline
     - send_submit/transaction_done、slot count
     - 等槽时间、在途数达到上限
   * - 时钟误差影响什么
     - observed 与 truth 对齐残差图
     - endpoint_ops、port windows、clock_models
     - 关联误差随 offset/drift/窗口变化
   * - 配置变化是否有效
     - A/B 差值图与置信区间
     - manifest、metrics、transfer quantiles
     - 唯一改变量与预测一致

分层下钻路径
------------

.. code-block:: text

   1. 请求延迟分布
       ↓ 选最慢请求
   2. 请求所在 wave / operation 时间线
       ↓ 选最慢 operation
   3. operation 的 src→dst 流量矩阵与 transfer 尾部
       ↓ 选最慢 transfer
   4. transfer 路径上的端口窗口
       ↓ 查看同窗其他来源
   5. business / background / control 归属与协议事件

这条路径避免一开始就看包日志，也避免只看服务平均值而找不到网络原因。

Observed-only 与 truth-assisted 两种界面
----------------------------------------

算法开发界面只能读取 ``observed/``：展示各设备本地时钟上的 operation 区间、端口计数窗口和公开配置，
并输出候选匹配及置信度。

评价界面在算法完成后才加载 ``truth/``：展示真阳性、误匹配、漏匹配、时间偏差、归属字节误差，
以及按 operation kind、rank、端口和时钟偏移分层的指标。

不要在同一个交互页面默认同时暴露两者，否则很容易把真值字段无意中用于算法调参。

建议新增的派生变量
------------------

以下变量可以从现有数据离线计算，无需修改协议：

* ``ready_latency_ns = recv_done_ts - submit_ts``；
* ``ack_tail_ns = transaction_done_ts - recv_done_ts``；
* ``operation_skew_ns = max(rank_complete) - min(rank_complete)``；
* ``port_utilization = counted_bytes * 8 / (window_ns * link_bps)``；
* ``path_overlap_count``：某窗口同方向共享端口的在途 transfer 数；
* ``incast_fanin``：目标 rank/端口窗口内不同源 rank 数；
* ``bytes_per_operation/window``：操作在端口窗口的线速占比；
* ``scheduler_feedback_delay``：wave 完成到下一次相关提交的间隔；
* ``clock_alignment_error``：算法推定对齐与 truth clock model 的残差。

其中利用率必须使用对应方向链路速率，不能把 payload bytes 与 wire bandwidth 混算。
