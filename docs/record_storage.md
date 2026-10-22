# 观测记录与存储格式

非UB新增可选的活跃逻辑字节额度、源QP序号和阶段耗时。`queue_metrics=true`为全部交换机出口增加常量空间的全程队列积分，独立于包记录截断及summary模式。旧`queue_events`保持原格式，不能直接解释为减字节后状态。字段、单位与覆盖范围见[准入示例](../examples/scaleout_rdma_admission/README.md)。

非UB的可选`packet_capture`与`fault_injection`存于`result.json`，属于全局时间和业务身份已关联的内部评价资料，不是策略输入。详细事件使用`columns`对应的数值数组，含设备发送、接收、接收错误丢弃和BEgressQueue入/出队；`complete`、`omitted_events`及`selection`说明所选范围是否完整。故障计数与详细采集开关独立，记录到达上限不停止传输。关闭详细记录且无故障时保留原结果字段。字段、序号、队列和包UID的准确口径及失败诊断入口见[丢包小例](../examples/scaleout_rdma_faults/README.md)。

启用软件准入或生命周期观测时，新增`admission`终态和消息资源时刻；可选`lifecycle_capture`为申请、获取、QP登记、数据齐备、ACK及发送映射移除/软件额度归还的增量。`rdma-check`独立核对完整记录及全部所有者，`rdma-diagnose`支持失败末态。截断时明确不重建历史，不把数据齐备解释为发送额度或GPU缓冲释放。字段、边界和快照上限见[准入示例](../examples/scaleout_rdma_admission/README.md)。

非 UB `/2` 的完整形状输入及独立输出见[百卡示例](../examples/scaleout_clos128/README.md)。`result.json` 保留原消息/端口语义，新增机内链路与显式执行限额；`queue_capture=summary` 只有观测计数和峰值，没有队列时序。`log/astra_nodes.log` 为完整 ASTRA issue/callback 审计源；校验优先读取它，不与重复的诊断滚动日志合并，文件存在但不完整会拒绝。兼容旧运行时才读取 `log/log*`，早期事件被覆盖的旧结果仍会失败，不补造事件。新 `/2` 结果用紧凑流式 JSON，旧 `/1` 保留原缩进格式。

非UB可选`route_replay`保存`route_evidence.json`及`routes/manifest.json`、所选原件快照，`input.json`使用运行内相对引用。`wave.json.routes`新增原层号和逐token来源（请求ID、文件ID、入口、token行、SHA-256）；`frontend.json`保存实际初始context，观察结束状态为`OBSERVATION_ENDED`，内部调度器有限长度状态另存`scheduler_state`。`capacity.json`在回放时逐rank列出按实际context计算的容量；无回放时保留原组级格式。独立核验先比对原件选择，再核算消息和执行因果。字段与完整流程见[来源回放示例](../examples/scaleout_route_replay/README.md)。

`scaleout topology` 的输出是静态物理输入，采用独立的 `moe-scaleout-physical-topology/1`、`moe-scaleout-topology-audit/1` 和 `moe-scaleout-topology-manifest/1`；不是本页的仿真观测或执行记录。图、NIC端口CSV、带宽/可达性审计及原生文本格式见[拓扑输出](../examples/scaleout_topology/README.md#输出与检查)，状态固定为 `generated_not_executed`。

本文件定义研究输出、原始执行记录、时间与字节口径，以及读取和资源预算。运行、采样与重放的操作步骤见[用户指南](user_guide.md)；完成事件和对象回收见[后端参考](../backend/README.md)。压缩与归档只改变保存方式，不改变业务消息、依赖和完成时刻。

## 网络机制事实

`moe-ub-observation-config/6` 在 `statistics`、`recording` 和有界存储之外接受 `network_facts`；可同时保留已有 `native_queue`、`send_state`。策略为：

```json
{"schema_version":"moe-ub-network-facts/1","ports":[{"node_id":4,"port_id":4}],"end_ns":100000,"max_records":100000}
```

端口上限 4096，覆盖从零开始，终点不超过仿真上限，记录数包括每端口的初始和最终记录。当前只支持独占 CBFC（基于信用的流控）。启用后不新增仿真事件、不重复调用仲裁，不修改队列、报文、路由或随机数。达到上限直接失败，不能把截断结果当成完整采集。

原始 `network_facts` 与导出的 `truth/network_facts.jsonl` 包含连续序号、全局 `time_ns`、节点/出口、事件和数据。`initial` 声明队列、各虚通道信用和每信用单元字节数；`voq`、`fifo` 记录等待字节变化；FIFO 入队/出队保留包身份；`tx` 记录实际发送开始、字节、有效速率和完成延迟（含已配置帧间隔）；`credit` 记录实际消耗及控制帧/捎带归还后的余额；`credit_decision` 记录真实队首检查的报文长度、需要与可用单元及是否阻塞。生成式端点队列不提供包身份，转发队列可提供当前包 UID。绕过信用检查的控制报文没有该判断事件。

`truth/network_facts_capture.json` 声明实际覆盖、完整性和记录数。`final` 是半开覆盖终点，边界状态从之前记录恢复；若执行先结束，则终点缩短到实际结束时刻。检查失败只证明该次队首无法通过信用条件，不能外推为连续阻塞；串行服务区间应取并集，不能把可能重叠的独立控制报文发送重复计时。信用余额非零也不一定够发送队首。

公开读取接口 `moe_ub_cosim.network_facts.checked_rows(execution, report)` 流式校验序号、覆盖、信用变更和报文所需单元数；须消费完整迭代器才能完成末尾检查。导出记录用 `stored_rows(truth, 'network_facts.jsonl')` 读取。它是准备/评价用内部事实，含包 UID，不能直接交给诊断程序；需另行生成明确白名单的观测输入。`slice inspect` 声明此能力，`sample` 保持原策略，缺失资料不能离线补造。完整命令见[原生小例](../examples/native_first_round/README.md)。这些仿真钩子不代表目标芯片已有对应接口。

## 哪些文件可以交给研究算法

`moe-ub run`、`sample` 和 `replay` 的研究结果采用相同的目录分工。算法仅以 `observed/` 为输入，其余目录用于复现、生成或评价。

| 目录 | 内容与用途 |
|---|---|
| `observed/` | 每 rank 的端侧操作、本地端口采样窗口和公开静态配置；不含全局操作号、传输号、请求身份、图节点、逐包来源或时钟变换参数 |
| `truth/` | 操作与端侧记录、传输、业务字节窗口的实际对应，以及内部时钟模型，供评价关联结果 |
| `metrics/` | 字节分类、守恒校验、存储和导出检查结果 |
| `inputs/`、`raw/`、`logs/` | 本次输入及摘要、执行计划与原始事件、运行资源和错误；是复现与排错资料，不属于算法输入 |

`sample` 复用的数据可能来自首轮数据包中的归档和工作缓存，未必在输出目录复制一整份 `raw/`。首轮数据包本身也含内部资料；共享算法输入时应选择其导出的 `observed/`，而不是直接传入整个数据包。[系统架构](architecture.md)说明这些记录如何从实际执行生成。

运行清单的 `coverage` 使用 `moe-ub-execution-coverage/1`，分别保存 `execution_complete`、`execution_budget_ns`、`simulation_end_ns`、`observation_complete` 和 `incomplete_ports`。观测完整指所选端口的本地半开窗口覆盖从时间零到实际完成时刻（包含该时刻的事件），不表示观测了全网。窗口之外没有记录，也不补零。此检查使用内部时钟参数，保存在执行资料中，不加入 `observed/`。原始后端另外记录 `execution_budget_ns` 和全局窗口的 `observation_coverage`；公开导出采用用户本地窗口重新计算覆盖，不能直接沿用后端采集范围。

## 公开表与字段

可选 `diagnostic_storage=gzip` 仅处理已退出后端的 `raw/execution/log/*.log` 文本。`raw/execution/diagnostic-storage.json` 使用 `moe-ub-diagnostic-storage/1`，逐文件保存原名、原始字节数与 SHA-256，以及 gzip 路径、大小与 SHA-256。完整解压校验通过后才释放对应文本副本；未完成清单或校验错误会使运行失败。它保留后端当时留下的全部日志字节，不补回此前已轮转淘汰的日志。结构化执行记录、研究输出及仿真事件不受影响。

原有两表观测采用 `moe-ub-research-storage/1`，新增操作阶段与队列观测采用 `/2`。每个输出范围有自己的 `storage.json`，声明逻辑表名、文件名、行数、原始与压缩字节、原始内容 CRC32，以及单条记录上限。实际表放在该目录的 `records/` 下。

| 逻辑表 | 字段 |
|---|---|
| `endpoint_ops.jsonl` | `rank_id`、`local_record_id`、`kind`、`start_local_ns`、`end_local_ns` |
| `port_windows.csv` | `switch_id`、`port_id`、`direction`、`local_window_start_ns`、`local_window_end_ns`、`counted_packets`、`counted_packet_bytes` |

`local_record_id` 只在一个 rank 内编号，不编码全局操作身份。端侧起止时间取实际节点与回调，并按通信模板确定 Dispatch/Combine 的操作范围；不能用首包或末包时刻代替。`direction` 当前为 `egress`，包数与字节数为非负整数，时间单位为纳秒。

`public_config.json` 使用 `moe-ub-observed/1` 或带显式数量限制的 `/2`，声明 rank、观测端口、本地时间范围、周期、时钟分辨率、计数位置和字节口径；不包含时钟偏移与漂移。旧版 `observed/` 的压缩格式只允许 `public_config.json`、`storage.json`、`records/` 及上述两张表；下文 `/3` 公开配置额外允许阶段与队列表，`/4` 再增加发送资源状态表。校验器不会为了验证它们访问 `truth/`。

**逻辑名以 `.csv` 结尾的压缩表，内部也是 gzip JSONL。** 例如 `port_windows.csv.gz` 解压后每行一个 JSON 对象，应按清单和项目读取器解释，不能根据后缀交给 CSV 解析器。早期未压缩研究输出则直接保存 `endpoint_ops.jsonl` 和真正的 `port_windows.csv`；二者的字段与统计含义相同，文件编码不同。

## 计数位置与字节口径

### 原生请求队列诊断

新的队列记录附带 `store_queue_policy`、`last_store_producer`，任务与内部身份附带 `producer`。游标表示最近成功形成请求的核，初始为 `null`；没有静态分配的旧 FIFO 任务，其核身份为 `null`。FIFO 的 `queue` 按任务顺序排列；轮转模式先按下一次选择的核开始循环，再逐核列出核内队列，不能把整个展开列表解释成严格的未来请求顺序。旧记录没有这些新增字段时仍按原 FIFO 格式读取。

可将嵌套策略改为 `moe-ub-native-queue-config/2`，保留原五个字段并显式增加正整数 `max_request_records`。这另外记录**整个执行、所有端点的全部原生 STORE 请求**，不受队列快照的 `rank_ids/end_ns` 选择限制，LOAD 不进入该流。超限立即失败，不截断。外层仍用 `moe-ub-observation-config/5`，总压缩与导出预算继续生效。

新增 `truth/native_requests.jsonl` 含连续 `sequence`、全局 `time_ns`、实际 `node_id`、`operation="STORE"`、任务内 `offset/bytes` 和 `identity`（图、传输、发送节点、源/目的 rank、操作、生产者）。它没有包 UID 或线上请求编号，便于按稳定业务身份比较字节范围。`native_queue_capture.json.request_audit` 声明完整覆盖、条数与实际结束时刻；只打开原策略 `/1` 时不生成该流。`moe_ub_cosim.native_queue.checked_requests(execution, report)` 流式读取并核验条数、顺序、完成传输覆盖、连续无重叠字节范围及字节总量；须消费完迭代器才能完成末尾检查。两种记录都属于内部真值，不是设备已具备的观测接口。

`moe-ub-observation-config/5` 在已有 `statistics`、`recording` 和预算之外接受 `native_queue`。可与 `send_state` 同时配置；未配置时不订阅、不生成请求队列流。配置必须恰好包含：

```json
{"schema_version":"moe-ub-native-queue-config/1","rank_ids":[2],"end_ns":100000,"max_records":100000,"max_snapshot_items":1024}
```

`rank_ids` 选择发起端，必须属于本次运行。所有被选端点的所有原生 worker（请求处理器）都被覆盖，从仿真零时刻开始，到 `min(end_ns, simulation_end_ns)` 为止。`end_ns` 不超过仿真上限；`max_records` 限制总行数及登记身份数；`max_snapshot_items` 限制每行队列与在途请求总数，上限 65536。另受原始存储、导出单行/压缩空间和宿主预算约束，任何超限均失败。较大的并发额度会增大每条快照，需预检，不应盲目开启全端点记录。

`truth/native_queue.jsonl` 使用全局 `time_ns` 和连续 `sequence`，包含 rank、实际节点与 worker 编号，STORE/LOAD 当前占用与额度、端点共享请求编号池容量、该 worker 的两个有序任务队列，以及整个端点的在途请求。每份任务含真实图、传输和 SEND 节点身份；每份在途请求含编号、任务字节偏移与长度。`affected` 指向触发该行的任务/请求。事件为：

| event | 实际读取位置 |
|---|---|
| `initial` / `final` | 零时刻初始状态 / 覆盖结束时的状态 |
| `task_queued` | 任务段进入 worker 队列后 |
| `request_issued` | 请求实际形成且占用 worker 额度后 |
| `outstanding_blocked` | worker 达到对应额度且队列仍非空的分支 |
| `request_pool_blocked` | API 分配共享请求编号失败的分支 |
| `response_released` | 响应释放编号和 worker 额度后，完成回调之前 |

若执行超过采集终点，`final` 使用终点之前最后一次真实状态，不新增仿真事件；状态可以非空。`truth/native_queue_capture.json` 给出覆盖、策略、条数和零丢弃声明。该流与 `observed/` 隔离，不能作为分析算法输入；它不是交换机排队或硬件流水线测量。通过 `iter_result(execution, 'native_queue')` 读取原始记录，通过 `stored_rows(truth, 'native_queue.jsonl')` 读取导出记录。`slice inspect` 声明此内部采集能力，`sample` 保留原采集策略和全局时刻；旧记录必须重新执行后端才能补齐。公共操作见[原生小例](../examples/native_first_round/README.md)。

### 端侧发送资源状态

显式输入 `moe-ub-observation-config/4` 的 `send_state` 开启采集；同时要求已有的 `statistics`、`recording`、观测预算和有界原始记录策略。省略该项时保留原版本行为。策略为：

```json
{"schema_version":"moe-ub-send-state-config/1","max_records_per_rank":100000,"max_total_records":1000000}
```

两项上限包含初始、更新和结束记录；每 rank 至少 2 条，总量至少覆盖所有 rank 的两个边界。超限使本次运行失败，不丢弃记录后宣称完整。原始和导出记录也计入各自已有的压缩字节、单行字节预算。

公开配置为 `moe-ub-observed/4`，压缩存储为 `moe-ub-research-storage/3`，在阶段和队列表之外增加 `endpoint_send_state.jsonl`：

| 字段 | 含义 |
|---|---|
| `rank_id`、`sequence` | 端点及其从 1 开始的连续顺序号，不编码图、节点或传输身份 |
| `time_local_ns` | rank 本地时钟的整数纳秒 |
| `event_kind` | `initial`、`update` 或 `final` |
| `ready_send_count` | 调度扫描后仍在实际可执行队列中的非 CPU `COMM_SEND_NODE` 数，不含尚未满足前驱的任务 |
| `active_send_count` | 实际发送资源集合的占用数量，到发起方 SEND 完成回调释放为止 |
| `send_slot_capacity` | 实际配置的槽容量；初始 0 表示尚未配置，不表示未知或丢记录 |

记录点位于完整依赖就绪扫描后及槽配置后，只有三项值发生变化时写 `update`。不记录扫描内部每一次临时占用/释放；不同扫描在同一纳秒发生时仍保留各次更新。同刻值不可任意去重或重排，计算正时间区间的状态应使用该时刻最后一条记录。本地时钟取整还可能让不同全局时刻映射到同一时间，顺序号继续有效。

采集覆盖整个服务，跨图不重置序号。`public_config.send_state.coverage` 按 rank 声明本地起止、记录数、`complete: true` 和 `dropped_records: 0`；零通信 rank 也必须有两个边界。初始状态在仿真零时刻、槽配置前读取。成功排空后，以已知零待发、零占用状态延续到声明的 `max_simulation_ns`，被动写入 `final`；它不推进模拟时钟，不代表实际完成时刻。容量在空闲尾段保留最后配置值。覆盖范围外未知，缺少边界或序号不连续不能补零。

原始结果的 `send_state` 压缩流使用 `time_ns`；`send_state_capture` 保存采集策略及全局覆盖。导出只对这些真实采集记录转换本地时钟，不从节点执行真值重建计数。公开校验只读 `observed/`，不依赖真值或时钟模型。`stored_rows(observed, 'endpoint_send_state.jsonl')` 顺序读取，内存使用与 rank 数量有关，不累计全量历史。

`slice inspect` 的 `moe-ub-capture-capabilities/2` 声明已捕获发送状态。`sample` 保持该策略与所有状态更新，可变换 rank 本地时钟；端口是否有详细包记录不影响已有发送状态的时钟变换。未捕获发送状态的旧数据必须重新执行后端，不能升级采样来获得。用户步骤见[发送状态小例](../examples/send_state/README.md)。

### v0.4 操作阶段与等待统计

新增统计采用独立格式：输入 `moe-ub-observation-config/2` 的 `statistics` 为
`{"schema_version":"moe-ub-observation-statistics/1","queue_waiting_bytes":true,"operation_stages":true}`，
并显式给出观测资源预算；其余时钟、端口和来源字段沿用原格式。公开配置升级为
`moe-ub-observed/3`，压缩观测存储为 `moe-ub-research-storage/2`，增加以下两张表。
当前交付检查状态见[任务记录](tasks/current.md)，此处规定字段解释。

`endpoint_stages.jsonl` 每行只有 `rank_id`、`local_record_id`、`stage`、`time_local_ns`、`event_count`。
记录仍使用本 rank 的局部编号，时间转换使用该 rank 的本地时钟。阶段含义如下：

| stage | 实际来源与计数 |
|---|---|
| `prepare_start/end` | Dispatch 的 prepare 或 Combine 的 pack/local_merge 计算节点；每项计数 1，无实际节点则省略 |
| `local_prepare_start/end` | 模板为同芯片交付设置的本地准备节点；与上行准备分别计数 |
| `request_submit_first/last` | 本 rank 发起、属于该操作的逻辑 UB 任务的最早/最晚提交；计数为任务数，不是分段包数 |
| `data_ready` | 该 rank 作为消费者收到的全部应用消息的最晚数据就绪回调；计数包括业务、应用状态和同芯片交付，LOAD 取响应消费方 |
| `initiator_resources_complete` | 本 rank 发起的逻辑 UB 任务的最晚原生事务完成；计数与提交数相同，不表示 LOAD 供数方的全部缓冲状态 |
| `local_publish_first/last`、`local_resources_complete` | 同芯片交付的实际发布与发送回调，计数为交付数，不伪造 UB 请求 |
| `operation_complete` | 原 `endpoint_ops` 定义的操作终点，取真实依赖执行，计数 1 |

没有对应事件则省略该阶段，不补虚构时间。准备可以早于旧端侧操作区间的开始；各类就绪与资源完成不相互替代。
这些是仿真器可见的节点与回调，实际硬件是否能采集同样信息尚未确认。

`queue_windows.csv` 前五项端口与窗口字段沿用 `port_windows.csv`，分别增加
`voq_waiting_byte_ns/mean_bytes/peak_bytes` 和 `fifo_waiting_byte_ns/mean_bytes/peak_bytes`。
面积为精确非负整数 byte·ns，均值为面积除以本地窗口实际长度，峰值为整数 byte。
后端原始面积以十进制字符串保存，公开 Python 读取结果为整数，避免 64 位乘积溢出。

| 对象 | 包含范围 |
|---|---|
| `voq_waiting` | 已完成入口处理、等待指定出口调度的 VOQ 包；包含本地生成并进入 VOQ 的信用控制包，排除 ingress processing |
| `fifo_waiting` | 已进入该出口 FIFO、尚未取出发送的包；排除正在链路上发送的包，直接发送而未入 FIFO 的控制包不计入 |

两种对象均按队列实际保存的 `Packet::GetSize()` 计字节，包含该位置已有的头部及控制消息，不等于业务有效字节。
每个入队、转移、出队事件更新被动计数；统计不调用调度函数、不插入仿真事件。
窗口为本地半开区间，面积累计状态持续时间；峰值包含窗口内每次变化后的状态，包括同一时刻持续零纳秒的中间状态。
左边界前遗留的状态只在窗口内继续保持时计入；恰在左边界出队且立刻变零的旧状态不冒充窗口内峰值。
持续积压跨窗拆分，末窗用实际长度。发送计数表和队列表分别稀疏保存：无发送但等待量非零的窗口仍必须有队列记录。
`logical_queue_windows()` 按队列清单恢复其声明范围内的零窗口，不能从发送表的缺行推断等待量为零。

后端 `queue-state-events.jsonl` 保存时间零初值及每次变化后的两个等待量；`queue-windows.jsonl` 直接累计本次声明窗口。
当前详细采集的再次采样独立重算状态积分，并在原窗口配置下逐项核对直接累计结果。只有发送事件、没有这些状态资料的旧记录不能恢复队列统计。

交换出口成功开始发送时，按当时的 `Packet::GetSize()` 将整包计入窗口。一次包发送全部计入开始时刻所在窗口，不按包在链路上占用的时间拆分到多个窗口。发送尝试、入队和发送结束不是本计数位置；未包含在 ns-3 包中的物理编码或 FEC 字节也不计入。

| 字节类别 | 含义 |
|---|---|
| `business_payload_bytes` | 模板传输的有效激活或专家结果等业务范围；传统 `attribution_windows` 表采用这一口径 |
| `application_metadata_bytes` | 量化比例、索引、计数或状态标志等应用数据 |
| `padding_bytes` | 模板布局填充及原生 LOAD 短尾响应中超出有效范围的体字节；内存窗口预留空间不自动计入 |
| `data_protocol_overhead_bytes` | 承载上述数据的包中，剩余协议头等字节 |
| `control_packet_bytes` | TA/TP ACK、信用等协议控制包的整包字节 |
| `unattributed_bytes` | 分类输出保留的未归类计数字段；当前有效执行不以它掩盖缺失的 WRITE 来源 |

应用状态 WRITE 可以只含元数据，仍是应用消息，不能因为没有业务向量就归入协议控制包。TA/TP ACK 即使带任务标签，其载体字节也不获得业务操作归属。旧模板没有应用元数据或填充时相应量为零；`metrics/validation.json` 声明本次是否输出填充字段。

原生 STORE 的业务范围来自请求体，LOAD 来自响应体；读取请求及原生 ACK 计入控制包，响应头计入数据协议开销。32 B 供数就绪通知属于应用元数据。原生 `transfer_registry` 另含 `operation`、`initiator_rank`、`data_owner_rank`、`consumer_rank`；`src_rank/dst_rank` 表示请求方向。`native_starts`、`native_completions` 分别保存实际内存任务的开始与完成，不能用空的 WQE 记录推断原生任务未执行。

每个包每次经过一个观测出口都计数。跨服务器路径可能经过多个交换出口，因此全网端口字节之和不是唯一交付业务量。唯一交付量从目标有效写入核对；逐端口计数与原生端口累计值核对；每窗分类之和与整包计数核对；来源开启时，该窗各操作归属业务字节之和再与业务分类核对。来源关闭会停用操作归属检查并记录 `attribution_status="disabled"`，不改变原有执行和整包观测。

`truth/attribution_windows.csv` 保存 `operation_id`、端口与方向、本地窗口起止和 `business_payload_bytes`。这里的字节量表示该操作在该端口、该本地窗口中实际经过的业务字节，量大于零才表示二者有真实关联。对应关系从本次包的任务与字节范围产生，不从端侧时间相近或端口流量形状反推。

## 持续聚合与选择性详细记录

输入 `moe-ub-observation-config/3` 在 `/2` 的统计和预算字段外增加 `recording`：

```json
{
  "schema_version": "moe-ub-observation-recording/1",
  "mode": "aggregate",
  "detail_ranges": [{"switch_id": 4, "port_id": 4, "start_ns": 2000, "end_ns": 36000}],
  "truth_categories": ["business_data", "attached_metadata", "dispatch_count", "combine_flag", "load_ready"],
  "max_detail_events_per_port": 10000,
  "max_window_attribution_entries": 1000
}
```

`aggregate` 模式直接在当前事件更新包数、各类字节、队列面积与峰值，仅在窗口结束时序列化汇总；不先生成全部逐包 JSON。`detail_ranges` 为空表示只存窗口聚合，非空则额外保存所选出口和全局半开区间内的详细资料。每个所选端口最多给一个详细区间，必须是本次观测端口；总数受观测端口预算限制。`detailed` 模式为所有本次观测端口保存 `[0,max_simulation_ns)` 的详细资料，此时 `detail_ranges` 必须为空。两种模式都保存相同的基础窗口汇总和端侧阶段。

详细范围使用内部全局时间，保存在输入和执行资料，不写入公开观测。详细事件数包括发包、FIFO 审计、状态快照/变化及分类来源；同时受原事件字节预算和压缩记录总预算约束。`max_window_attribution_entries` 限制单端口当前窗口中的“操作、消息类别”条目数，达到上限报错，不丢弃记录。合并窗口导出使用独立导出策略的操作总数限制。操作与协议必要的在途状态始终存在，关闭详细保存不会关闭协议状态。

应用消息分类仅用于 `truth/` 的评价记录：

| `message_category` | 来源 |
|---|---|
| `business_data` | 激活、专家结果或 KV 的业务字节区域 |
| `attached_metadata` | 数据消息内实际复制的 scale、rank/token/topk 等元数据区域 |
| `dispatch_count` | CANN Dispatch 的计数发布，包括零贡献状态 |
| `combine_flag` | CANN Combine 的完成标志 |
| `load_ready` | 原生 LOAD 前真实发送的供数就绪通知 |

`truth_categories` 显式选择保留类别，关闭 `record_provenance` 时必须为空。`truth/message_attribution_windows.csv` 每行包含端口、方向、本地窗口起止、`operation_id`、`message_category` 和 `application_bytes`；其格式与选择声明在 `truth/message_categories.json`。零业务状态消息的归属来自实际传输的消息角色和字节区域，ACK、信用与响应头不会取得这些应用类别。传统 `attribution_windows.csv` 仍只保存所选 `business_data` 的投影，不改变原业务关联定义。保留部分元数据类别时，不宣称其和等于全部应用元数据。

后端采用 `moe-ub-port-records/3`，基础 `aggregate-windows.jsonl` 同时保存计数与字节分类，`queue-windows.jsonl` 保存队列面积与峰值，`truth/message-windows.jsonl` 保存所选类别的操作归属。只有明确选择详细区间的端口才有 `packet-events.jsonl`、`egress-queue-audit.jsonl`、`queue-state-events.jsonl` 和可选 `truth/classified_packet_ranges.jsonl`。清单声明具体范围及事件数/字节预算，不把汇总称为完整逐包来源。

新版队列详细历史用 `event_kind=initial/update/final` 区分区间初值、实际变化和结束快照。初值是左极限状态，不制造零时长峰值；结束快照关闭最后一段持续时间，队列在该详细区间结束时可以仍有积压。快照在下一个实际事件或最终导出时补记已知状态，不插入仿真事件；只有实际执行结束且网络排空后，才允许将后续声明区间记录为零状态。

`sample` 在创建输出目录和展开归档前检查能力：只有基础窗口时，要求交换机时钟保持、起止边界对齐、周期为原周期的整数倍，窗口范围不超出保存范围。包数和面积求和、峰值取最大、均值用合并后的实际长度重算。移动边界、细分或改变交换机时钟，要求选定端口有覆盖整个请求区间的发包与队列状态资料；按整数时钟反求边界，避免忽略映射到同一本地时刻的先前事件。端侧完整区间和阶段仍由已保存的实际时刻转换。

`slice inspect` 的 `capture_capabilities` 显示保存类型、基础窗口、详细范围、队列状态和真值类别。带新策略的数据包使用 `moe-ub-research-slice/2` 并校验能力声明；旧 `/1` 数据按原详细保存解释，采样范围和时钟能力保持，不能新增未采集的队列统计或消息类别。采样保持原捕获策略及类别选择；需要改变它们时重新执行 `run` 或 `replay`。各次输出只对自己 `public_config.ports` 声明的本地范围有效。

## 本地时钟、窗口与零值

执行只使用 ns-3 的全局时钟，被动统计和导出将其转换为设备本地时钟：

```text
local_ns = floor((1000000000 + drift_ppb) * global_ns / 1000000000) + offset_ns
```

`offset_ns` 为有符号纳秒，`drift_ppb` 为十亿分之一的漂移比例，整数计算向下取整。每个 rank 和每个被观测交换机恰有一份时钟，同一交换机的端口共享该时钟；校验正时钟比例和整数范围，保证转换结果单调不递减且不溢出。内部时钟与全局时刻保存在 `truth/clock_models.json` 和对应记录中，不写入公开表，也不改变发包或计算调度。

端口窗口使用本地半开区间 `[start, end)`。落在右边界的发送属于下一窗口；结束位置不足一个周期时仍保留较短的最后窗口。端侧记录保存操作的完整起止时刻，跨越端口观测边界的操作不会被裁剪成另一条操作。

压缩输出只写有包的窗口，`storage.json` 中的 `window_encoding="nonzero_with_declared_zero_windows"` 和 `logical_windows`，结合公开端口范围与周期，声明其余窗口均为零。清单覆盖范围之外表示未观测，不能补成零。已声明范围内的空窗口是确定的零值，不能当成数据缺失。

仿真实际执行区间也与观测声明区间分别保存。若一轮在 75 ms 左右结束而观测声明到 1 s，后面的窗口可为零，但不能据此声称仿真还执行了更多轮次。目标场景的实际区间与使用建议见[CM384/R1 场景](scenarios/cm384_r1.md)。

## 读取与校验

[research_storage.py](../python/moe_ub_cosim/research_storage.py)提供以下常用接口：

| 接口 | 返回或检查的内容 |
|---|---|
| `stored_rows(directory, table)` | 逐条读取实际保存的压缩表；端口表仅返回非零窗口 |
| `logical_windows(observed_directory)` | 按公开配置逐窗恢复完整端口表，包括零窗口和不足周期的尾窗 |
| `logical_queue_windows(observed_directory)` | 对含队列统计的新版观测逐窗恢复等待量；不依赖该窗是否发包 |
| `validate_observed_storage(observed_directory)` | 检查目录白名单、清单、公开字段、非零窗口顺序与边界、端侧记录及文件完整性；只读该 observed 目录 |

计算总字节或非零窗口数时可以只遍历实际保存的行，例如在已安装项目的 Python 环境中：

```python
import json
from pathlib import Path
from moe_ub_cosim.research_storage import stored_rows

observed = Path("runs/demo-10us/observed")
metadata = json.loads((observed / "storage.json").read_text())
nonzero_windows = 0
total_bytes = 0
for row in stored_rows(observed, "port_windows.csv"):
    nonzero_windows += 1
    total_bytes += row["counted_packet_bytes"]
zero_windows = metadata["logical_windows"] - nonzero_windows
print(total_bytes, nonzero_windows, zero_windows)
```

`logical_windows` 适合必须使用完整时间序列的算法。完整网络的逻辑窗口数可能很大，流式接口节省内存，但仍要遍历每一窗；不要先转换为包含全部窗口的列表。非零窗口校验可以直接完成，不需要展开数十亿零行。

读取器检查文件大小、gzip 完整性、记录数、原始字节、CRC32 和单条记录上限。迭代器必须消费到结束才完成末尾核验；读取前几行不等于整份文件已通过。CRC32 用于损坏检测，来源及版本由运行清单中的 SHA-256、输入记录和版本信息另行核对。

`moe-ub validate --observed` 使用上述公开检查；`moe-ub validate --run` 还核对运行清单、文件摘要及已保存的导出校验结果。它不重跑仿真，也不独立证明两个执行完全相同。对照实验的等价性需要比较其实际执行和逻辑观测，已有检查与证据见[首轮验收记录](../fixtures/m10_research_slice/README.md)。

## 后端原始记录

服务输入中的 `record_policy` 指向 `moe-ub-record-storage/1`；底层服务也可通过 `--record-storage POLICY.json` 指定。策略包含以下正整数，未知字段拒绝：

| 字段 | 限制 |
|---|---|
| `max_compressed_bytes` | 原始压缩记录文件的累计内容字节 |
| `max_buffer_bytes` | 共用压缩缓冲的有效载荷上限，最大 1 GiB |
| `max_record_bytes` | 单条原始记录上限，最大 64 MiB；缓冲还必须容纳额外的 64 KiB 压缩块 |

开启后，`execution/result.json` 使用 `moe-ub-service-result/2`，`record_storage.fields` 引用 `execution/records/` 中的 gzip JSONL；没有该策略的旧入口保留 `/1` 数组结果。按图保存的文件在清单中按提交顺序排列，避免多组乱序完成改变原数组的顺序；WQE 启动、完成与目标写入按实际回调顺序记录。

[record_storage.py](../python/moe_ub_cosim/record_storage.py)的 `iter_result(execution, field)` 兼容这两种结果，`iter_port_records(execution, port, name)` 读取端口原始记录并按其清单恢复逻辑空窗口。端口的 `record-storage.json` 使用 `moe-ub-port-records/1` 或增加队列资料的 `/2`，登记表由共享 `registry-records.json` 引用，避免每个端口复制一份操作和传输表。原始逐包事件、FIFO 队列记录、包字节对应和 ASTRA issue/callback 都是内部执行证据，不直接充当算法输入。

`RecordStore` 共用有限缓冲，按块压缩，不为每个端口常驻文件句柄和压缩器；一个文件允许包含多个标准 gzip member。写入前核对压缩预算，失败后不能继续产生成功结果。空记录在封存时创建，之后禁止追加；可通过硬链接复用相同空内容，但各逻辑文件仍分别计入记录策略的压缩字节限额。后端记录缓冲字节只表示有效载荷，不能作为进程 RSS。

## 导出和宿主资源预算

研究导出使用 `moe-ub-research-export-policy/1`，所有字段为正整数。`max_compressed_bytes` 限制 observed、truth、metrics 中压缩记录的合计内容，`max_index_bytes` 限制临时传输索引；`max_record_bytes`、`max_endpoint_records`、`max_operations`、`max_layouts`、`max_id_ranges`、`max_graphs`、`cache_rows` 分别约束单条记录、端侧排序表及各种索引和缓存。单条记录最大 64 MiB。

导出逐条核对计划、交付和业务来源，每个网络传输使用 33 B 定长磁盘索引（其中 1 B 检查原生任务生命周期，旧版为 32 B）。mmap 页面仍计入宿主 RSS；端侧排序和操作、布局表也需要内存，不能把“流式导出”解释为固定且可忽略的内存消耗。通过全部校验后先写 `metrics/index-cleanup.json`，再删除可重建索引；失败保留原因，不写成功的 `validation.json`。

端口采集另有 `moe-ub-observation-resources/1` 数量预算，约束端口数、每端口和总逻辑窗口数、每端口事件字节、包来源条目、登记传输数及导出字节。这些数量与记录策略均不能代替外层宿主预算。普通用户配置中的 `host` 限制覆盖运行阶段实际拥有的子进程与输出根目录：

| 字段 | 计量范围 |
|---|---|
| `rss_bytes` | 周期采样的所属进程树 RSS 合计，包括脱离会话的后代；共享页可能重复计入 |
| `address_bytes` | 每个子进程的地址空间上限，不是整个进程树的总内存上限 |
| `artifact_bytes` | 已登记产物根目录累计占用的上限；另设置单文件大小限制 |
| `wall_seconds` | 当前受监测阶段的现实时间上限，不是仿真时长 |
| `min_free_bytes`、`artifact_roots` | 保留的可用磁盘与一同计量的其他目录 |

外层磁盘监测按设备号和 inode 去重硬链接，跨多个登记根目录也只计一次；不跟随输出符号链接。分别累计唯一文件的内容大小以及文件和目录分配块字节，再取两项总量中的较大值。因此它与“各逻辑记录分别计入压缩内容预算”的内层规则不同，不能互相替代。清单、图、原生诊断日志和目录分配空间也需要外层覆盖。

RSS 和磁盘为周期采样，可能遗漏采样间隔内的短暂峰值；地址空间和文件大小限制为每进程限制。报告中的采样峰值不能宣称为精确物理峰值。运行和导出是分别监测的阶段，均成功才生成完整成功交付；提高一个内层限额不会自动提高其他限额或重试。实现见[host_probe.py](../python/moe_ub_cosim/host_probe.py)。

## 图归档与首轮数据复用

底层完整模型服务的 `--graph-storage POLICY.json` 必须与 `--record-storage` 一同启用。`moe-ub-graph-storage/1` 的 `max_recipe_bytes`、`max_archive_bytes`、`max_active_graph_bytes` 分别限制单图原始构图输入、累计归档和活动图占用。构图在短生命周期 Python 子进程中执行完整生成与校验，服务进程保留编号及路由消费状态，不长期持有已完成大图。

图结束后先确认归档及原文件摘要，写入 `graph-replay/gN/cleanup.json`，再删除已登记且可重建的活动文件。出现额外文件、内容变化或归档损坏时拒绝清理，不能连同未知文件删除。构图前的完整组合校验也在独立进程进行，保留输入和检查摘要。图释放的事件条件由后端参考定义，压缩和磁盘清理不能提前触发 WAVE_DONE。

以下两种复用用途不同，均已保留：

| 入口 | 使用已有资料的方式 |
|---|---|
| `python -m moe_ub_cosim.graph_storage replay` | 从构图输入归档重新生成计划和 Chakra，重新执行校验，并核对源码、固定 proto、计划及图摘要；本身不执行网络 |
| `moe-ub replay` | 从 `slice create` 保存的完整首轮图和初始条件直接运行后端，不重新调用请求 Scheduler、导入原始路由或生成图 |

`moe-ub sample` 则只读取首轮数据包的实际事件，在已覆盖的端口与时间范围内改变采样，不执行上述两种流程。首轮清单绑定输入、来源、图、执行事件、网络与观察范围、随机配置和文件摘要。当前只支持从明确初态执行完整首轮，不恢复任意中途网络状态；改变批次、路由或计算图的实验须重新运行相应前端。

原设计的观测范围与统计要求见 [v0.2 §10](moe_ub_cosim_dev_v0.2.md)，首轮复用边界见 [v0.3 §4.9](archive/moe_ub_cosim_dev_v0.3.md)。存储改造和规模测量的具体结果保留在[资源工程记录](../fixtures/m10_resource_efficiency/README.md)和[首轮验收记录](../fixtures/m10_research_slice/README.md)，不在本格式说明中重复实验过程。


## A3 内部局部完成事实与有限层段

`research-slice/4` 是有限层段 `/3` 的无损压缩保存形式：`archives` 恰好含一个 `execution_archive`，目的目录为 `raw/execution`；图和全部输入仍为原文件。压缩包有独立逐文件清单，`compaction` 保存原切片清单 SHA-256、保存全部原始字节的声明和执行文件数，新 `artifacts` 绑定压缩包、清单及未压缩文件。压缩时逐成员验证并再次检查原切片；`slice inspect --verify` 核验新包全部文件。重放不解压历史执行，采样使用已有校验展开路径。该变化只改变存储，初始网络、层段、依赖和完成边界不变。

A3 资源模板的原生 STORE 结果在 `transfers` 行内附加 `local_facts`（`moe-ub-transfer-local-facts/1`）。`local_read_complete_ns` 是该传输所有原生请求已实际取得编号并形成请求包的时刻，作为参考模型的局部读完成事件；`business_ready_ns` 和 `application_metadata_ready_ns` 分别来自接收方实际 `RequestArrived`，按计划中业务/应用元数据字节区间计算。类别没有字节时为 `null`。填充字节不计入这两类。协议 ACK 回收继续用 `transaction_done_ns`，不与局部复用合并。这些是内部仿真事实，不是硬件可采集接口或 HBM 时序测量。

既有操作级 `data_ready` 保持业务、应用状态及同芯片交付的汇总含义；不能解释为最后一个业务数据包到达。逐数据解释使用新增局部字段，并结合 `message_role` 区分附带元数据和独立 count/flag 消息。

公共 `facts` 命令从封存计划及有界结果记录导出选定范围，格式和预算见 [示例策略](../examples/a3_segment/facts.json)。`truth/nodes.jsonl` 记录原层号、rank、资源名、直接前驱、前驱完成最大值 `dependency_ready_ns`、实际起止时间和事件种类。`ready_to_start_ns` 包含资源与调度等待，不能全部标成网络排队。`truth/deliveries.jsonl` 记录实际数据所有者和消费者、任务/有效字节、来源、提交、局部读、业务与状态到达、全部载荷就绪及协议回收；同芯片行明确 `delivery=same_chip`，没有虚构网络包。

两张表使用现有 `moe-ub-research-storage/1` 压缩清单，通过 `research_storage.stored_rows(DETAIL / 'truth', 'nodes.jsonl')` 或 `deliveries.jsonl` 读取。`facts.json` 声明选定 graph/rank/层、实际数量、输入摘要和限额。一次只加载一个不超过 `max_plan_bytes` 的计划，保留选定节点及其直接前驱；结果记录逐行读取。内存、时间和累计磁盘另受配置 `host` 约束，超限不会产生成功清单。明细不写入 `observed/`，离线导出不会再次运行仿真。

有限层段输入为 `configured-service/5`，其 `execution.segment`（`moe-ub-model-segment/1`）声明原始连续层、Decode 步骤、请求上下文、入口与 KV 已就绪、空网络从 0 ns 开始和完整部署容量。对应计划为 `model-segment-plan/1`，部署为 `service-deployment/6`，保存包为 `research-slice/3`。相关计算只覆盖所选完整块，不执行 embedding、末尾归一化、lm_head 和 sampling。结果 `completion_scope=selected_segment_only`，前端一轮推进仅是执行驱动，不是完整生成 Token；回放范围为 `dependency_driven_frozen_finite_segment`。

通过已核验文件准备的层段在 `inputs/preparation.json.route_evidence` 保留来源修订、文件摘要、原始层和行号、请求上下文及精度差异。`run.json.input_evidence` 绑定准备结果；运行检查服务与层输入摘要，并核对来源记录覆盖和实际专家选择。图与运行清单据此区分 `published_model_trace` 和合成输入，保存包携带同一证据。运行和回放无需重新读取原始轨迹文件；改变路由或范围须重新准备。紧凑 JSON 只省去空白，不改变字段和核验条件。

## 非 UB Scale-Out 恢复记录

显式 `recovery.mode=go_back_n` 时，`result.json.recovery` 保存配置、逐消息源端状态、超时/重复确认计数及有界事件。默认原生模式不增加此字段。`highest_sent_bytes` 是实际发送最高字节，`acknowledged_bytes` 只来自真实累计 ACK；`timeout_retransmissions` 为超时重传决策数，不含原生 NACK 重传，不等于包数。`retries_since_progress` 为上次 ACK 进展后的次数；`deadline_ns=-1` 表示未计时，可能是当前已发字节都已确认、正常完成或重试耗尽，须结合状态判断。

源事件列为 `[time_ns,event,source,message_id,acknowledged_bytes,highest_sent_bytes,retries_since_progress,rto_ns,deadline_ns]`，事件码随 `event_codes` 导出。`receiver_events` 保存接收端本地的重复包偏移和重新确认序号，具体列随输出提供。两类事件共享 `max_events`；累计观察数与遗漏数独立保存。完整日志才重放计时、退避、确认和终态；截断时 `rdma-diagnose.recovery.reconstructed=false`，仍检查计数与原生终态，不补造缺失事件。

`inflight_packets` 为当前已发出但尚未被下一跳接收/丢弃的逐跳包数，只用于最终排空，不参与发送端恢复。服务完成还要求源计时器、发送 QP、软件额度、交换机队列和 ACK 队列排空。`failure.reason=recovery_retry_exhausted` 带失败消息与时刻；`simulation_time_limit` 单独表示预算到期。失败时保留原始未完成消息与占用额度，不把它们填成正常完成或释放。

`scaleout rdma-diagnose` 离线读取这些字段；它与端口、接收端、图依赖的关联属于评价真值，不能直接输入在线策略。公开配置和运行见[恢复示例](../examples/scaleout_rdma_recovery/README.md)。
