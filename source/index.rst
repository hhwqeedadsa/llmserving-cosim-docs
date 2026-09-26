LLM Serving、ASTRA-sim 与 MoE-UB 联合仿真
=============================================

这套文档把三个层次放在同一张地图里：ASTRA-sim 提供通用执行图与通信执行机制，
LLMServingSim 在其上增加 LLM 服务语义，我们的联合框架进一步把非均匀 MoE/P-D
通信展开到 UB 协议网络，并增加端网观测和独立真值。

.. admonition:: 最新研究进展 · 2026-09-26

   已将同一个 transformer block 24 的 AllReduce、AllGather、ReduceScatter 闭环到
   ns-3 task、packet、switch hop、queue 和 port trace；并完成 55 项 AIDC 推理测量、
   仿真与优化工作审计，形成三项可证伪研究主线。详见
   :doc:`daily/2026-09-26`、:doc:`aidc-inference-tail-survey`；全部每日记录见
   :doc:`daily/index`。

.. raw:: html

   <div class="comparison-strip" aria-label="三个系统的核心定位">
     <div><strong>ASTRA-sim</strong><span>执行图、资源依赖、Collective / SEND / RECV</span></div>
     <div><strong>LLMServingSim</strong><span>请求、连续组批、KV、模型层、Profile、服务指标</span></div>
     <div><strong>MoE-UB 联合框架</strong><span>逐对流量、UB 竞争、完成语义、端口观测与归属真值</span></div>
   </div>

核心结论
--------

* **LLMServingSim 是 ASTRA-sim 的服务系统前端，不是新的网络协议仿真器。**
  它把请求、调度、KV 状态和模型层转换成 Chakra 图，再让 ASTRA-sim 返回本轮耗时。
* **联合框架不是 LLMServingSim 的完整超集。** 它减去了上游较完整的类 vLLM 调度、
  TP/PP/CXL/PIM 等广度，换取逐 Token 路由、逐源—目的传输、UB 包级竞争和端网归属粒度。
* **目前最强的证据是工程正确性与因果语义。** T01–T14、字节守恒、观测隔离和可复现性
  已验证；真实路由、目标硬件计算 Profile 和实际通信运行时模板仍需校准。
* **规模上限必须按保真度讨论。** LLMServingSim analytical 路径已在 EP=320 跑通；
  联合框架的 ns-3-UB 包级路径尚未完成 EP=320 基准，不应把小型 fixture 的正确性外推成
  大规模可承受性。

文档导航
--------

.. toctree::
   :maxdepth: 2
   :caption: 每日研究记录

   daily/index

.. toctree::
   :maxdepth: 2
   :caption: 系统与数据

   architecture
   io-contracts
   experiment-inputs

.. toctree::
   :maxdepth: 2
   :caption: 竞争与观测

   communication
   observability

.. toctree::
   :maxdepth: 2
   :caption: 专题研究与可视化

   simai-llmservingsim-study
   aidc-inference-tail-survey
   visualization

.. toctree::
   :maxdepth: 2
   :caption: 边界与复现

   boundaries
   reproduce

证据口径
--------

本文使用三种标签：

``已验证``
   有本地运行、自动测试或固定输出支持。

``接口已具备``
   数据结构和执行路径存在，但尚未由真实数据校准。

``研究假设``
   用于探索的模型或外推，不能当作真实部署结论。

示例运行 ``m5_t12`` 完成 7 个请求，包含 392 个 rank 侧操作记录、368 条传输，
仿真结束于 629,673 ns。该运行使用合成路由、参考计算 Profile 和
``direct_rank_dedup_v1`` 参考通信模板，因此适合说明数据链路与竞争，不适合声明真实硬件绝对性能。
