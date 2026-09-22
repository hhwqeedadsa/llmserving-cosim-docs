一次实验需要给系统什么信息
============================

一次可解释实验至少需要八类信息。缺少其中任何一类，系统可能仍能运行，但结论会退化为
未受控假设。

信息清单
--------

.. list-table:: 输入、作用与缺失后果
   :header-rows: 1
   :widths: 18 28 28 26

   * - 类别
     - 必要信息
     - 决定什么
     - 缺失或不准的后果
   * - 请求负载
     - 到达时间、input/output tokens、请求/会话关系
     - 排队、batch 组成、prefill/decode 压力
     - 吞吐和尾时延没有业务含义
   * - 模型结构
     - 层序、hidden、专家数、Top-K、KV 布局和 dtype
     - 计算节点、激活/KV 字节数
     - 流量和容量同时错误
   * - 并行与部署
     - 实例、角色、rank、TP/PP/EP/DP、专家放置
     - 谁计算、谁通信、跨哪条链路
     - 热点位置和瓶颈判断错误
   * - Token 路由
     - request/phase/token/layer→expert IDs
     - 非均匀 ``n_ij`` 和专家负载
     - 只能研究合成分布，不能声称真实模型行为
   * - 计算模型
     - device/dtype/op/shape→时长及校准来源
     - 计算—通信重叠、到达网络的时刻
     - 绝对延迟和竞争窗口错误
   * - 通信模板
     - 去重、分块、channel/peer、同步和完成语义
     - 传输数量、依赖和发送并发
     - 即使网络准确，输入给网络的 workload 仍可能错误
   * - 网络系统
     - 拓扑、路由、带宽、时延、队列、流控、协议参数
     - 路径、排队、拥塞、ACK 和尾部
     - 只能得到理想化通信时间
   * - 观测与实验契约
     - 计数 hook、窗口、时钟、seed、版本、对照变量
     - 可见字段、可复现性、归因评价
     - 容易泄漏真值或混淆因果

LLMServingSim 最小输入
----------------------

LLMServingSim 的典型运行需要：

``cluster config``
   节点/实例、硬件、内存、TP/PP/EP/DP、链路带宽和时延，以及 P/D、CXL、PIM 等开关。

``model config``
   HuggingFace 风格的模型结构，包括层数、hidden size、attention heads、专家数和 Top-K。

``workload JSONL``
   ``input_toks``、``output_toks``、``arrival_time_ns``；prefix cache 研究还需要 token IDs。

``profile bundle``
   ``dense.csv``、``per_sequence.csv``、``attention.csv``、MoE 的 ``moe.csv``，
   可选 ``skew.csv/skew_fit.csv`` 和 ``meta.yaml``。

``运行策略``
   max sequences、max batched tokens、chunked prefill、prefix cache、routing policy、dtype 等。

LLMServingSim 会从这些信息自动生成 ASTRA network/system/memory 配置和每轮 Chakra 图。

联合框架附加输入
----------------

联合框架需要比 LLMServingSim 更明确的通信和观测信息：

``service.json``
   根配置：实例、角色、batch 上限、chunk、输入文件、计算模式、观测配置。

``requests.jsonl``
   每请求还要声明 decode 实例、源 rank，以及可选的 prefill 实例与源 rank。

``routes.jsonl``
   每个实际会执行的 ``(request_id, phase, token_index, layer_id)`` 都必须有 expert IDs；
   缺失即失败，不回退为均匀分布。

``placement.json``
   每个专家的 owner rank 和 rank→UB node。当前 fixture 建议 ``rank_id == node_id``。

``compute.csv``
   ``op,phase,device,dtype,layer_type,tokens,time_us``。正式实验应覆盖实际 shape，
   而不是依靠最近邻外推。

``network/``
   node、topology、routing table、transport channel 和 network attributes。

``clocks.json``
   端点与交换机的 local clock offset/drift；它只影响观测窗口，不反馈业务执行。

``来源声明``
   route source、calibration status、communication profile basis、seed、依赖 commit。

示例根配置
----------

.. code-block:: json

   {
     "schema_version": "moe-ub-input/1",
     "run_id": "my_case",
     "max_batch_per_wave": 4,
     "chunk_bytes": 65536,
     "transport_profile_id": "URMA_WRITE_RTP",
     "compute_mode": "profile",
     "compute_profile": "compute.csv",
     "device": "target-npu",
     "compute_dtype": "bf16",
     "profile_out_of_range": "error",
     "model": "model.json",
     "placement": "placement.json",
     "requests": "requests.jsonl",
     "routes": "routes.jsonl",
     "instances": [
       {"instance_id":"P", "role":"prefill", "rank_ids":[0,3],
        "kv_capacity_bytes":1073741824},
       {"instance_id":"D", "role":"decode", "rank_ids":[1,4],
        "kv_capacity_bytes":1073741824}
     ],
     "observation": {
       "enabled": true,
       "counter_hook": "egress_packet_start",
       "sample_period_ns": 1000,
       "interval_ns": [0, null],
       "clocks": "clocks.json",
       "clock_set": "research_offsets"
     }
   }

输入到通信需求的推导
--------------------

对于一个 MoE 层，系统按以下顺序推导网络输入：

.. code-block:: text

   已组批请求
     → 展开本轮每个 token
     → 查询 token 在该层的 expert_ids
     → 用 placement 得到每个 expert 的 owner rank
     → 同一 token 对同一远端 rank 去重
     → 聚合 n_ij
     → bytes_ij = n_ij × hidden_dim × dtype_bytes + metadata
     → 按 chunk_bytes 分块
     → 每块生成一对 SEND/RECV 和一个 transfer

本地命中只增加专家计算输入，不进入网络。Combine 在当前参考模板中按本地合并结果返回源 rank；
其他运行时若采用逐专家复制、分层转发或不同同步点，必须定义新的模板版本。

运行前检查
----------

* 请求引用的实例和 rank 存在；P/D rank 不重叠；
* 路由完整、Top-K 正确、expert ID 不越界；
* 所有被命中的专家都有 placement；
* Profile 覆盖最大 batch/expert token 形状；
* KV layout 和容量一致且不隐式转换；
* ``chunk_bytes`` 不超过 UB 任务字段上界；
* 网络路径确实经过想研究的瓶颈，且方向一致；
* 观测窗口和本地时钟不参与路由、QoS、流控或完成逻辑；
* manifest 中明确标记真实数据、校准数据和研究假设。

输入证据等级
------------

.. list-table:: 结论应该跟随最弱的输入证据
   :header-rows: 1
   :widths: 18 32 25 25

   * - 等级
     - 路由/计算/通信/网络
     - 可以回答
     - 不可以回答
   * - 功能 fixture
     - 合成路由、固定计算、参考模板、测试拓扑
     - 字节、依赖、守恒、因果是否正确
     - 真实系统绝对性能
   * - 校准外推
     - 真实局部 Profile + 公开网络参数
     - 趋势、敏感性、候选机制
     - 未覆盖区域的精确 P99
   * - 小规模闭环
     - 真实路由/运行时 + 2–8 端点对照
     - 明确范围内的误差预算
     - 未验证的 EP=320 长轨迹
   * - 大规模验证
     - 多保真校准 + 规模基准
     - 指定部署和 workload 下的研究结论
     - 超出配置、协议和数据范围的泛化

