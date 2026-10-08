长时间波形与模型—网络证据复核
==============================

2026-10-08 更新。复核既有 **6 组请求实验、11 组网络竞争实验、1 组长窗口 replay**；
本日没有新增 GPU 实测或 ns-3 仿真。重新检查原始输出、代码与配置，并修复图和报告的
三处边界问题：放大窗口不完整、queue 峰值口径不清、384 设备映射不能直接执行。

一、多个波峰、波谷已经有证据
----------------------------

.. figure:: _static/round10/long-window-network-traffic-waves.svg
   :alt: 0到1100微秒内十次通信突发及九段突发间空闲区间
   :class: study-figure

   上：每方向分类型 data wire bandwidth；中：两个核心出端口队列；下：通信 task 窗口。

:数据来源: ns-3 AllPacketTrace、PortTrace、QueueTrace、task_statistics；``research/round10/``。
:物理量: 带宽 Gbps（5 μs 分箱）、队列 KiB（1 μs 采样）、first-packet 至 last-ACK 窗口 μs。
:证据类型: ``ns3-simulated/trace-derived``，不是 A100 实测。
:边界: 10 次释放时间人为指定；9 段波谷不是已观测 GPU 计算。黑线是 25 μs 居中移动平均。

20 个 task、1302 个 data packets；5,434,548 wire bytes 在逐包表与带宽积分之间守恒；
每方向不超过 100 Gbps。W1/W2 放大数据从原先的 100--260 μs 补齐至 100--280 μs，
完整覆盖图示的 275 μs 尾段。详见 :doc:`long-window-network-contention`。

二、各项要求的证据落点
----------------------

.. list-table:: 逐项复核，研究结论与硬件结论分开
   :header-rows: 1
   :widths: 22 50 28

   * - 要求
     - 已检查的证据与结果
     - 限制
   * - 模型参数、大小
     - JSON 独立求和：30.532B 参数、BF16 56.871 GiB；48 层、128 experts、Top-8
     - 非实测显存；active 3.353B 是参数表口径
   * - 输入大小
     - 128/1024/4096 × 4/32，6 个原始请求输出、110 份 batch trace 与公开总账一致
     - 单请求、固定 TP2/EP2
   * - GPU 类型
     - RTX PRO 6000 Blackwell Profile；meta.yaml 显示单 GPU profiler、dummy 权重、TP1/2 shape 网格
     - 非本轮 A100 或真实多卡 benchmark
   * - 连接与分层带宽
     - analytical 16 GB/s、20 μs；八 host 两 switch，access/core 400/100 或 100/400 Gbps
     - 16 GB/s = 128 Gbps；不等于实机链路
   * - 参数能否调整
     - cluster link_bw/link_latency 支持标量与等长维度列表；实际调用校验器检查
     - network.yml 每次重建，不能只手改生成文件
   * - 384 设备适配
     - DP64×TP2×PP3、stage 内 EP128 的配置校验通过
     - 未运行 384-rank workload，无 NPU/物理拓扑校准
   * - 网络竞争
     - 登记的 11 个 case、63 个完成 task；方向、竞争字节比、热点预测均符合输出
     - 只覆盖受控拓扑，不外推生产 p99
   * - trace 与 CCL
     - 已生成 trace + trace_generator + Chakra converter + ASTRA Workload 源码核对
     - peer 是 ring 投影，不是 NCCL/HCCL channel trace
   * - 最细粒度
     - 278,528-B peer → 5 WQE segments → 68 个 4-KiB packets → hop/queue/port
     - 333.92 ns 是满包序列化时长，不是时钟精度

模型与实验完整结果见 :doc:`model-hardware-trace-contention`。

三、Trace 为什么与本工作有关
----------------------------

Trace 把“模型正在做什么”保留下来，才能将网络竞争关联回某个 block 或 collective：

.. code-block:: text

   请求/模型/硬件 Profile → scheduler batch → operator/collective rows
     → Chakra COMM_COLL_NODE → ASTRA collective algorithm
     → 显式 peer 投影：src/dst/bytes/phase/dependency
     → ns-3 traffic.csv → task/WQE → packet → hop/queue/port

128-token、EP2、BF16 dispatch 在当前代码中是
``(128//2) × (2048+128) × 2 = 278528 B`` 的 AllGather；combine 是
``128 × 2048 × 2 = 524288 B`` 的 ReduceScatter 总 buffer。
两 rank ring 的 RS peer 为每方向 262144 B。逻辑字节、peer payload、wire bytes 要分开。

实际 LLMServingSim CCL 路径是 TP AllReduce、MoE AllGather + ReduceScatter，
转为 Chakra 节点后由 ASTRA 执行 ring/localBWAware。旧注释提到 AllToAll，但不代表
本次实际输出；也没有执行真实 NCCL/HCCL。Block24 为零起编号 24（顺序第 25 个），
由 ``layernorm_289`` 及原解析器 ``(op_index-1)//12`` 确认，不是一个 batch 或请求。

四、384 设备与带宽参数的可执行边界
--------------------------------------------------

.. figure:: _static/round09/huawei-384-logical-mapping.svg
   :alt: DP64 TP2 PP3共384逻辑设备以及各流水阶段共享EP128
   :class: study-figure

   通过当前配置校验器的候选。每个 stage 为 64 instances × 2 TP ranks，三个 stage 共 384 台设备。

:数据来源: config_builder 实际调用与 ``research/goal-audit-20261008/audit.json``。
:物理量: device/rank/block count；这是逻辑关系图，不是物理链路测量图。
:证据类型: ``config-validator-only/design-hypothesis``。
:边界: 改用 PP3 后不等价于“三独立副本”；未执行大规模仿真，未校准华为硬件。

旧 DP3×EP128 / DP3×TP2×EP64 草图按三实例 DP group 配置均被拒绝：EP 不能被 3
整除，而且 TP/EP 共享设备。正确设备数来自 DP×TP×PP，不能再乘 EP。

对于当前候选，ASTRA dimensions 为 ``[2,3,64]``，EP mask 为
``[true,false,true]``。cluster ``link_bw`` (GB/s) 和 ``link_latency`` (ns) 可使用
等长列表；校验器也会拒绝列表长度不匹配。此处逻辑维度仍不自动映射节点内/外，需要
另外固定 rank placement，在 ns-3 的 topology.csv 中逐链路设置带宽与时延。

换成 NPU 必须替换计算 Profile，并取得设备/节点内、超节点内、超节点外的物理连接与
带宽资料；不能通过改一个设备名或统一 bandwidth 就称为华为 384 超节点仿真。

五、修正、验证与未做事项
------------------------------------------

* 修复多导出一个带宽 bin、放大窗口尾部不完整、局部 queue 峰值范围误指全局；
  新增 6 项分析回归，全部通过。
* queue 全回调记录峰值是 670.325/212.099 KiB，含同刻中间更新；旧同刻最后状态峰值
  为 670.265/208.369 KiB；0.25 μs 采样峰值又是另一口径。都不是硬件 buffer counter。
* 对照原始请求 CSV、110 份文本 trace、保存的 ET/config、11-case 全 task 输出与
  长窗口 packet 重新验证，逐份输入与关键代码 SHA-256 保存在 sources.csv。
* 未做：真实多机校准、NCCL/HCCL channel/chunk 捕获、384-rank workload 执行、
  生产请求到达驱动的完整模型长时间波形。本次测试不证明这些能力。

完整 Markdown 报告及审计程序在仓库 ``research/goal-audit-20261008/``。
可下载 :download:`Markdown 报告 <../research/goal-audit-20261008/report.md>`、
:download:`审计结果 JSON <../research/goal-audit-20261008/audit.json>`、
:download:`来源 SHA-256 清单 <../research/goal-audit-20261008/sources.csv>`。
复核命令（工作区需保留原始实验）：

.. code-block:: bash

   PYTHONDONTWRITEBYTECODE=1 python3 research/goal-audit-20261008/verify_evidence.py \
     --workspace /path/to/a100_ep_runtime

本次完成的是所请求的长时间可视化、必要仿真实验、模型/网络参数调研、trace/CCL 路径与
粒度说明；不是把“可配置”“可观测”当成“已校准硬件”。
