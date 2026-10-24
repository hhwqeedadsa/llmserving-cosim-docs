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

本模式独占Fabric传输入口，不能混用旧Send、软件准入、旧式recovery/drop_once、lifecycle_capture或机内bypass。支持DCQCN、ACK间隔1及正常原生NACK处理；显式持久恢复及故障配置见下节。v1仍限制rank≤31；v2按协议的`max_gpus`及实际图检查端点，协议v1默认32GPU。协议v2虽支持更大的图，持久入口本次只验收8GPU上的24连接/192工作，不代表千卡已验证；固定交换机计数器的内存检查和宿主RSS上限仍生效。

内部按唯一线端端口查连接，以有序累计前缀处理发布、接收和ACK，实际发送序号通过二分查找工作边界（允许原生NACK回退）。依赖只在真实回调发生后释放，以输入顺序处理同时就绪的工作。它们减少宿主扫描，不修改包、QP信用、网络时间或路径规则；没有新增网络性能收益主张。

## 输出与预算

### 网络回调与本地处理交替执行

将运行命令的`--config`换为[local-stages.json](local-stages.json)，即可使用`moe-rdma-stream-work/3`。它在同一ns-3时钟内执行“接收/ACK→本地处理→后续写”，保留等待期间的连接、字节序号和拥塞状态，无需填写后续通信的绝对开始时刻。

v3保留v2的显式`limits`，增加必填`local_model`：

```json
"local_model": {"mode": "serial_per_gpu/1", "duration_source": "declared_ns"}
```

所有工作须填写`kind`。`write`使用`id/kind/stream/bytes/after`；`local`使用`id/kind/rank/duration_ns/after`。`rank`须是图中真实存在的GPU，`duration_ns`为0到协议`max_sim_ns`之间的整数，单位ns，不接受布尔值。每个GPU只有一个抽象串行处理槽，非抢占；不同GPU可并行，网络独立推进。该槽不预留显存、SM或NIC资源。v3的`limits.max_work`统计写和本地节点的总数，最高仍为8192。

依赖只引用输入中较早的工作。写工作提供`accepted/acked`，本地工作只提供`completed`；跨类型事件、未知字段和绝对`ready_ns`均由CLI拒绝。本地工作在实际前驱全部完成后排队，按就绪入队顺序执行；同一回调释放的一批工作按输入顺序，同纳秒的不同回调保留ns-3事件顺序。已经排队的工作优先于刚被解锁的工作。未来写在其实际前驱完成前不会预留发送槽或发布字节。

人工示例包含3次写、5项本地处理。GPU4上两项工作由同一次接收解锁并串行执行；GPU1独立执行400,000ns，后续写还须等它完成。零时长工作依然通过实际仿真事件完成。最后一项本地工作晚于最后ACK，连接继续保留到全部工作完成并且网络物理排空。

`result.json`仅在v3增加`local_model`、`local_work`、`completed_local_work`。`local_work`按输入顺序保存`id/rank/duration_ns/ready_ns/start_ns/completed_ns`；三个时刻分别是实际入队、开始和完成，未发生为-1。顶层`completed_work`统计写和本地节点，`persistent_transport.work`仍只包含真实写。到达仿真时限时命令失败但保留结果前缀，已安排的未来完成时间不会写成已完成。v1/v2没有这些新增字段。

这是**声明耗时的功能模型**，没有GPU实测、CUDA并发、现代DeepEP调用或GIN可见性。`accepted`仍只是连续接收前缀，不能据此认定GPU已可读。改变`duration_ns`可检查间隔与网络状态的相互作用，不能当作更换A100/H100或实际MoE计算的预测。包级因果、队列、旧格式兼容与时限检查见[本地阶段验收](../../fixtures/local_stages/README.md)。

### 可选的独立拥塞反馈参考模式

工作输入可增加`dcqcn_reference`；完整示例见[work-reference.json](work-reference.json)。运行时把上面命令的`--config`换成这个文件即可。
省略该字段保留原有逐包ACK反馈模式。持久字节入口及v5事务/事务会话可显式配置该参考；事务配置见[事务指南](../scaleout_rdma_transactions/README.md#选择重传下执行实际事务)。尚未接入在线LLMServingSim服务或现代GIN，不表示完成了生产RoCE仿真。

所有字段均须显式填写：

| 字段 | 单位和约束 | 作用 |
|---|---|---|
| mode | 固定`figure7_reference/1` | 文献机制加明确建模选择，不是网卡型号 |
| np_interval_ns | 1—max_sim_ns，ns | 目的端每流通知生成间隔；示例50,000 |
| alpha_interval_ns | 大于np_interval_ns且≤max_sim_ns，ns | 无新通知时alpha衰减；示例55,000 |
| recovery_interval_ns | 1—max_sim_ns，ns | 发送端恢复timer；示例55,000 |
| recovery_bytes | 512—1,073,741,824，B | 实际发出模型帧的字节恢复阈值；示例10,000,000 |
| min_rate_bps | 正整数、≤源NIC速率 | 当前速率下限，单位bit/s |
| additive_bps / hyper_bps | 正整数、各≤10^15 | 加性/固定超增步长，bit/s；目标速率不超过源NIC速率 |
| fast_recovery_rounds | 1—100 | 图7中的F；按触发后两个独立计数器判定阶段 |
| gain | 数字且0＜gain≤1 | alpha的EWMA增益；示例1/256 |
| cnp_model_bytes | 30—512，B | 原生CnHeader＋IPv4＋PPP＋填充的总模型帧长；示例48 |
| max_events | 1—100000 | 反馈事件日志的硬上限，超限失败，不静默截断 |

`min_rate_bps`、两个增速步长也各限≤10^15。整数不接受布尔值。N、K、恢复周期、字节阈值、g、F的示例值参考DCQCN 2015；最小速率与固定增速步长是显式声明值，**整套配置不是原论文部署复刻，也不是A100/H100/NIC校准表**。原生帧省略实际RoCE头、Ethernet开销和NIC处理代价，不能直接由本例延迟判断生产收益。

目的端仅根据实际收到的CE数据包产生通知：首次立即生成，周期内多标记保留pending，周期末按pending生成；空闲后重新立即通知。NP周期从生成/入队开始，实际发送还要竞争本地和反向网络。恰好到期的标记统一先结算旧周期、再归入新周期，避免回调插入顺序改变通知数。其他RP同纳秒事件按ns-3事件FIFO顺序执行，并保留事件记录顺序。

独立CNP使用IP协议255，实际经过原生高优先级队列、序列化、传播和ECMP。固定上游对CNP遗漏了哈希第三字的初始化；平台`CnpSwitch`只在局部选路元数据中投影为ACK分支，采用CNP源/目的IP和重复的fid作为两端口输入。包本身仍是255，不更改负载、字节或优先级；默认数据/ACK选路继续用原生路径。这个规则是本平台的明确选择，不能当作生产交换机的RoCE哈希字段。

发送端只在实际CNP到达后按原文式1降速/更新alpha，并重置恢复timer、字节剩余额及两个阶段计数器。在源端`PhyTxBegin`之后的原生`PktSent`阶段按整帧模型长度累积字节，每个阈值推进一次字节阶段；不是逐字节或尾比特发完时采样，timer独立推进时间阶段。按图7先递增触发方计数，再以`max(T,BC)<F`判快速恢复、以`min(T,BC)>F`判超增，其余为加性恢复。速率除法向下取整并钳制上下限；超增采用声明的固定步长，没有补入未核对的QCN加速规则。

本模式关闭旧ACK-CNP控制，避免同一反馈被两套控制器处理；ACK仍只推进确认前缀/队列信用，CNP不释放工作或创建完成。QP空闲时保留状态；所有预期工作ACK后还须排空pending NP通知、在途反馈及设备队列，才取消空闲timer并关闭。**最后工作ACK与顶层物理排空finish可能不同**，不能将控制尾部算成业务等待。

`persistent_transport.dcqcn_reference`记录有效配置、格式/选路/时钟范围及有界`events`，包含生成、实际发送、到达、alpha、两种恢复和关闭事件；按数组顺序解释同纳秒状态。旧顶层`rdma_parameters`仍描述原生对象属性；启用参考模式后，控制器有效值应读取`dcqcn_reference.config`，不能拿未使用的原生默认恢复周期解释结果。

可同时开启下节的被动速率采样；新增事件3/4分别是独立CNP处理前后，序号0不表示确认前缀，旧名`ack_cnp`在这两类事件里仅表示“这是通知”，不是ACK标志。ACK事件1/2的通知位必须为0。功能oracle、真实反向排队及旧模式对照见[参考模式验收](../../fixtures/dcqcn_reference/README.md)。

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

## 可选的有界选择重传

将上述公开运行命令的`--config`换为[work-selective.json](work-selective.json)，`--protocol`仍用`examples/scaleout_rdma/rdma.json`。该正常小例含两个连接及“实际ACK→100,000ns本地处理→17B追加写”；主动丢包需要在协议中另填下节的`faults`。已有二进制须包含本功能。

`transport_recovery.mode`设为`selective_repeat/1`，保留下节的`initial_rto_ns/max_rto_ns/max_retries/max_events`，可选Karn估计和`on_exhausted`，再显式增加两个字段：

| 字段 | 约束与含义 |
|---|---|
| receive_buffer_bytes | 每连接乱序有效负载容量，MTU到2MiB；必须≥`inflight_bytes + payload_bytes - 1` |
| inflight_bytes | 每连接原生固定发送窗口，MTU到2MiB；按包起点判窗，最后一包最多越过窗口`payload_bytes-1`字节 |

须同时提供完整`dcqcn_reference`，且`max_events`为1—100000。整数不接受布尔值。示例窗口4,000B、缓冲8,192B、初始RTO80,000ns；这些是功能参数，不是校准的带宽时延积。每连接最多保留4096个乱序包、发送侧最多4096个确认区间；另受原有工作、源字节、事件和宿主预算约束。

接收端保留实际乱序包，仅在缺口填上后推进连续前缀。真实ACK/NACK携带16B大端`MSR1`扩展（magic、收到区间的begin/end、valid）；源端只依据已收到的反馈跳过已确认范围，每个累计缺口最多快速重传一次，其余等待实际确认或RTO。选择确认本身不释放工作信用，累计ACK才释放；反馈丢失仍可能造成重复发送。在途及交换机排队包不会被取消。

扩展替换了原模型ACK的14B填充，故实际ACK/NACK模型帧为50B，较原48B增加2B；不能将16B扩展直接写成净增16B，也不能据此推算生产RoCE开销。该参考基线也可在v5事务及事务会话中有序交付实际MRT1负载，见[配置与范围](../scaleout_rdma_transactions/README.md#选择重传下执行实际事务)；它没有接入完整在线LLMServingSim或现代GIN，不是IRN复刻。

启用后`persistent_transport.selective_repeat`包含容量、窗口、`states`和`events`。状态含连续接收前缀、缓冲当前/峰值字节、拒收数及源已确认区间；事件含真实数据到达、反馈生成/到达和快速重传，时间单位ns、区间为连接累计字节。`extension_hex`保存反馈的实际扩展字节。事件上限与恢复记录分别执行，超限明确失败。独立逐包、队列、完成回调检查与限制见[选择重传验收](../../fixtures/selective_repeat/README.md)。

## 可选的持久连接丢包恢复

把运行命令的`--config`换为[work-recovery.json](work-recovery.json)，`--protocol`换为[rdma-loss.json](rdma-loss.json)。完整示例有两个持久连接和六项写工作；它在真实接收端丢弃stream0对前999B的首次ACK，检查实际重传、接收端补ACK及后续累计序号。故障是否命中看`fault_injection.drop_counts`，示例应为`[1]`。已有二进制须包含本功能，否则重新使用公开`rdma-build`构建。

工作根字段`transport_recovery`全部显式填写：

```json
"transport_recovery": {
  "mode": "go_back_n/1",
  "initial_rto_ns": 80000,
  "max_rto_ns": 640000,
  "max_retries": 3,
  "max_events": 10000
}
```

`initial_rto_ns`为首次等待确认的超时，范围1到`max_sim_ns`；`max_rto_ns`不小于初值且不超过1秒。`max_retries`为1—16次连续无进展重发，下一次到期记为耗尽；`max_events`为0—100000条被动恢复记录，设0仍执行协议但记录不完整。收到新的累计ACK才重置重试与定时；重复ACK不延期。指数退避保留原生pacing、队列、PFC和DCQCN。没有启用该字段时，默认行为保持。

可选`rtt_estimator: {"kind":"karn","min_rto_ns":20000,"granularity_ns":1}`沿用源端保守采样。最小RTO不能超过初值，粒度不能超过最大RTO。每个连接的估计证据在空闲后保留；不提供跨连接history种子。数值是声明参数，不能直接当作生产推荐。

协议中的`faults.message_id`在此模式指**stream ID**，`sequence`是整条连接累计字节偏移，跨工作边界不一定是MTU整数倍。它不是work ID；事务请求stream为`connection*2`，响应为`connection*2+1`。规则可匹配实际端点或交换机接收位置，未命中不会补造丢包。未开启持久恢复时继续拒绝faults；旧式recovery、drop_once不适用于此入口。

发布新工作只扩大合法范围；首次实际TX才启动超时，源端实际已发高水位约束ACK，迟到累计ACK允许超过重传游标。已确认前缀可以空闲后追加，QP不会因前缀ACK而关闭。重复数据只在接收端补发真实累计ACK，不重复交付给上层。必须等全部工作、在途数据/反馈及队列排空才Close。耗尽或到仿真时限时命令失败并保存部分result，不自动扩大预算。

输出`persistent_transport.recovery`包括各stream的已发布/实际已发/ACK前缀、截止时间、重试、可选RTT估计、重复ACK计数及失败原因。`events`沿用源端恢复列名，其中`message_id`同样表示stream；`prefix_published`是事件5，`complete`仅表示当前已发布前缀确认，不表示连接关闭。记录完整性由`complete/omitted_events`给出。`source_packets`统计全部实际重传，`inflight_packets/physical_drained/closed`区分排空与关闭。可与显式独立CNP参考模式组合。

同一字段也支持事务v1—v5及v4/v5在线会话，真实字节重传不重复执行远端put/fetch-add。MRT1仍是功能编码，接收不等于GPU可见。能力与边界见[平台验收](../../fixtures/persistent_recovery/README.md)；这不是新拥塞控制或恢复算法的性能结论。

`transport_recovery` 可选 `"on_exhausted":"isolate_stream"`，让一条源端单向流耗尽重试后，其他流仍在同一网络上执行；默认值 `stop_simulation` 保持原先全局停止。故障源从本地发送调度器移出，保留连接、真实序号和未确认工作。失败后才就绪的工作仍可在原有有限预算内入队，但保持未发布；不会重新发包或释放信用。未完成的事务源读租约继续保留。已经发出的包仍可能在目标写入或产生真实回复；迟到 ACK/NACK/CNP 仅记录，不恢复失败源。对端不会因此自动获得错误通知。

使用 [work-isolation.json](work-isolation.json) 和 [rdma-isolation.json](rdma-isolation.json) 可检查同一GPU/NIC上的两条连接：stream0耗尽后冻结，stream1在100微秒才就绪并继续完成；另有一项故障流工作留在队列中。沿用本页运行命令替换 `--config/--protocol`，预期退出1，查看输出目录的部分 `result.json`。这是人工故障功能例，声明的超时与本地等待均非硬件测量。

隔离失败仍退出1并保存部分 `result.json`，不表示整个任务成功，也不能正常Close。传输结果中的 `recovery.failures` 列出全部本地源失败，旧 `failure` 保留首个失败；`source_isolation` 分别记录 `wire_idle`（在途包、设备队列与待生成通知排空）、`healthy_registered_work_drained`（已入队的健康流工作已ACK）和 `failed_resources_released:false`。健康条件不包含尚未满足依赖而未入队的工作。`terminal_feedback` 保存有界迟到反馈，`terminal_feedback_packets` 为总数，`terminal_feedback_complete` 表示是否完整。线端空闲不代表远端退出证书，也不代表失败源资源可复用。见[隔离验收](../../fixtures/source_stream_isolation/README.md)。
