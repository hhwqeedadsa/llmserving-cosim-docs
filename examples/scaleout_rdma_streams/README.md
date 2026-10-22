# 持久原生RDMA字节传输小例

本例在一套锁定ASTRA原生ns-3中执行多次连续传输，后续工作由真实接收或ACK回调触发。同一连接在空闲后保留QP对象、累计序号和拥塞状态；发送队列条目按ACK归还。它用于检查持久传输、工作边界和反压，不执行LLMServingSim、现代DeepEP、原子signal或GPU可见性，也未做硬件时延校准。

## 执行

使用已有构建工具和固定依赖；不安装或下载。`rdma-build`的`backend.json`新增`stream_probe`二进制路径，原生构建仍与UB独立。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-build --output build/native-streams
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output runs/stream-topology
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology runs/stream-topology --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_streams/work.json \
  --backend build/native-streams/output/moe-rdma/ns3.42-moe-rdma-stream-probe \
  --output runs/stream-result
```

若实际输出位置不同，以`backend.json.stream_probe`为准。CMake/Ninja不在PATH时，构建支持`--cmake`、`--ninja`；已有Protobuf可通过`--protobuf-prefix`指定。只使用已安装工具。CLI路径相对cwd，输出目录须不存在、父目录须存在。已有可用原生二进制时无需重新构建。

## 输入与事件

`moe-rdma-stream-work/1`包含`streams`和`work`，完整输入见[work.json](work.json)。需要超过原小例计数时使用`moe-rdma-stream-work/2`，在根对象增加明确的`limits`，其他字段及事件语义相同。例如：

```json
"schema_version": "moe-rdma-stream-work/2",
"limits": {"max_streams": 24, "max_work": 192}
```

这是完整JSON对象中的两个字段，不是可独立执行的配置。连接与工作仍须填写；v2不自动创建流量或提高宿主/观测预算。`max_streams`支持1—128，`max_work`支持1—8192；v1保持16/64且拒绝`limits`。超过声明数量在执行前拒绝，原生二进制也检查数量，失败不自动扩大预算。

| 字段 | 意义与当前范围 |
|---|---|
| streams | v1为1—16条；v2不超过`limits.max_streams`。id唯一，src/dst为不同GPU，plane明确；初始提供给NIC的字节为0 |
| wire_port | 10000—65535、全局唯一的线端端口，整个连接不变；影响常规ECMP，不由工作ID更换 |
| source_nic/destination_nic | GPU物理设备端口，必须与地址/plane唯一可达的正向及反向NIC一致；多候选时拒绝，不能用哈希替代绑定 |
| queue_depth | 1—4096内的2的幂；按ACK释放的功能额度，不是实测NIC/CQ或GPU缓冲深度 |
| work | v1共1—64条；v2不超过`limits.max_work`。id唯一、引用已有stream，bytes为1—1MiB，每连接累计≤64MiB |
| after | 最多64项不重复依赖，引用输入中较早工作的accepted或acked；空列表在0ns就绪，无预设后续时刻 |

`accepted`表示原生接收端连续字节前缀覆盖该工作，不表示GPU可见。`acked`表示源收到覆盖该工作的真实累计ACK；本例据此释放队列条目，不当作现代GIN flush或生产CQ精确语义。提交先reserve，槽可写后才prepare/publish；只有已发布字节增加QP的可发送前缀。

默认6次传输共31,457B。work1等work0接收后就绪，深度1使其等ACK后才发布；后续工作检查空闲后追加。另一连接同时向相同目的GPU传输，共享同一网络状态与时钟。999/1001B等尺寸检查包边界；14,464/14,400B也只是操作数，不是一次现代MoE调用。拓扑的A100标签没有GPU测量含义。

本模式独占Fabric传输入口，不能混用旧Send、软件准入、恢复/故障、lifecycle_capture或机内bypass。支持DCQCN、ACK间隔1及正常原生NACK处理；没有通用ACK超时重传。v1仍限制rank≤31；v2按协议的`max_gpus`及实际图检查端点，协议v1默认32GPU。协议v2虽支持更大的图，持久入口本次只验收8GPU上的24连接/192工作，不代表千卡已验证；固定交换机计数器的内存检查和宿主RSS上限仍生效。

内部按唯一线端端口查连接，以有序累计前缀处理发布、接收和ACK，实际发送序号通过二分查找工作边界（允许原生NACK回退）。依赖只在真实回调发生后释放，以输入顺序处理同时就绪的工作。它们减少宿主扫描，不修改包、QP信用、网络时间或路径规则；没有新增网络性能收益主张。

## 输出与预算

### 可选的源端速率观测

需要核查拥塞反馈时，在工作输入JSON根对象增加以下字段（v1/v2均支持），其他`streams/work`保持完整：

```json
"source_rate_capture": {"start_ns": 0, "end_ns": 5000000, "max_events": 10000}
```

它是`--config`中的字段，不放入RDMA协议配置。`start_ns/end_ns`为同一仿真时钟的左闭右开区间，须满足`0 ≤ start_ns < end_ns ≤ max_sim_ns`；`max_events`为1—100000的整数，布尔值不算整数。省略时不保存速率序列。使用原来的`rdma-stream-probe`命令运行修改后的输入即可，无需新增命令。当前仅该字节入口提供采样开关，事务/在线服务仍只报告下面的有效属性。

开启后，`persistent_transport.source_rate_capture`保存`columns/samples`：源数据包`PhyTxBegin`时采样一次，每个收到的ACK/NACK在原有处理前、反馈后各采样一次；没有新增定时器或仿真事件。字段包含packet_uid、字节序号、模型包长、ACK的CNP位、QP当前/目标/最大速率（bit/s）、`alpha`、恢复阶段、累计发送/确认/发布前缀以及`next_avail_ns`。事件名称及码保存在`event_codes`，数组须按列名解读，不按固定偏移猜测。

TX钩子发生在该包的`PktSent`更新之前：`next_avail_ns`仍是此前状态，不是本包发出后的下次发送预测。ACK的后采样发生在队列`Pump`前，不包括随后可能发布的工作。`final_states`按`state_columns`读取，是报告时刻的状态，不受采样时间窗限制；可能包含空闲期间降速。没有周期性采样定时器内部的每次变化，不能视为连续速率轨迹，也不能把QP速率当实测速率或吞吐率。

计数到上限后再遇到合格事件时明确失败，保留执行stderr；不会静默截断、扩大上限或用不完整采样冒充完整记录。这与允许截断的`packet_capture`不同。采样上限之外，原有宿主、仿真、结果JSON与产物预算仍生效。数据属于离线诊断材料，不是在线策略的可读观测。

所有原生Fabric入口的顶层`rdma_parameters`记录每个GPU端`RdmaHw`初始化后、QP创建前实际读回的属性值，包括未覆盖的MinRate、RPTimer、alpha/降速周期、恢复次数和增速量；`units`说明单位，`fabric_overrides`列出适配器显式设置项，其他项来自固定原生默认值。速率单位bit/s，三个控制周期的单位是**微秒**。此增量没有修改默认值或新增DCQCN调参接口。不能将默认RPTimer=1500us或MinRate=100Mb/s当作当前硬件校准结果。

### 传输记录

`result.json.persistent_transport`保存连接状态、每工作`ready_ns/published_ns/accepted_ns/acked_ns`、累计`begin/end`序号、被动源包记录和物理排空状态。纳秒来自同一个Simulator。按已有端点与序号离线关联工作，不向包头添加工作ID；包不会跨越工作边界。

`qp_creations=1`表示一个连接对象只创建一次；`scheduler_reinsertions`计空闲成员被原生调度器移出后恢复的次数，不是重建QP。全部预期工作ACK、在途帧与设备/ACK队列排空后才关闭连接并取消其拥塞计时器；不能将“QP map非空”等同活跃传输，也不能因应用完成提前删连接。

目录另含`inputs.json`的命令、原生版本和输入/二进制hash，以及`execution/`的stdout、stderr与资源监控。工作输入JSON最多8MiB。沿用协议仿真时间上限；宿主最多60秒、RSS1GiB、地址2GiB、产物64MiB，卷预留8GiB。被动Tx观测最多200,000项（所有物理端口Tx，非仅源数据包）；超限/无法排空保留失败，不自动扩限或覆盖重跑。计数上限不是保证其任意组合均能在这些预算内完成。

人工独立核算、深度对照、并发根工作、重复和非法绑定的实际执行见[验收](../../fixtures/persistent_streams/README.md)。完成时间只描述此功能模型，不能将加深未校准队列后的变化写成优化收益。

索引修改前后同输入结果、显式计数和多轮扩展检查见[扩展验收](../../fixtures/indexed_streams/README.md)。
