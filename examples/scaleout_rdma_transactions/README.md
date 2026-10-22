# 远端地址与原子返回值的功能小例

本例验证“实际请求到达目标后才修改内存，原子旧值实际返回后才完成”的执行关系。两个源端共享同一张原生 Scale-Out 网络，执行 put（写入）与 fetch-add（取旧值并加法），目标用访问密钥和地址定位窗口。所有请求、返回值和传输确认使用同一个 ns-3 时钟；不执行 GPU、现代 DeepEP 或 LLMServingSim。

**用途是验证功能，不能用作 GIN/RoCE 时延基线。** 当前 MRT1 是明确的模拟报文格式，借助已有原生 UDP/SeqTs 字节载体，返回值另走反向持久连接并有普通 ACK。真实 RoCE 原子响应、优先级、头部、CQ、并发额度及内存可见性均未等价实现。程序没有给 NIC 或 GPU 补一个未经测量的固定延迟。

## 运行

使用已有工具和锁定依赖，不安装或下载。`rdma-build` 返回的 `backend.json.transaction_probe` 是本入口；构建仍与 UB 隔离。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-build --output build/native-transactions
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output runs/transaction-topology
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/transaction-topology --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/work.json \
  --backend build/native-transactions/output/moe-rdma/ns3.42-moe-rdma-transaction-probe \
  --output runs/transaction-result
```

路径相对 cwd；输出目录不能已存在，父目录须存在。实际二进制位置以构建清单为准。支持已有构建工具的 `--cmake/--ninja/--protobuf-prefix`，详见[构建与持久传输](../scaleout_rdma_streams/README.md)。已有相应可执行程序时可直接运行。

## 输入

`moe-rdma-transactions/1` 的完整人工输入见 [work.json](work.json)。它不是 MoE 路由或生产请求。

| 字段 | 含义与支持范围 |
|---|---|
| connections | 1—8个上下文；id 0—7唯一，src/dst 为不同 GPU、rank≤31，plane 显式声明 |
| request_port / response_port | 各自固定、所有连接间唯一，10000—65535；每方向持久创建一个 QP，不能作为真实 RC 的双向协议等价物 |
| source_nic / destination_nic | 按地址/plane唯一可达的物理设备端口；反向交换绑定。多候选或不可达时拒绝 |
| queue_depth | 每上下文应用工作额度，1—32内的2的幂。原子需等请求ACK和返回值，且按已提交顺序回收；不是实测 CQ 深度 |
| windows | 1—16个窗口；owner 为 GPU、key 为非零32位远端密钥、address 为64位地址、hex 为1—65536B的初始内容、allow_atomic 为布尔权限 |
| operations | 共1—32项；id仅供本地记录，connection引用上述上下文，key/address定位目标窗口 |
| verb=put | hex 是1—32768B小写十六进制。完整事务到齐时一次修改目标窗口；不模拟部分DMA写入或源GPU buffer寿命 |
| verb=fetch_add | value 是0—2⁶⁴−1增量；目标地址必须8B对齐并允许原子。窗口按小端64位值执行模2⁶⁴加法，返回修改前的值 |
| after | 依赖输入中较早操作的 applied、request_acked 或 completed，无固定ready时间。根操作在0ns提交 |

同一owner的窗口地址不得重叠，key不得重复；窗口不在运行中注销或重新注册，没有 epoch/跨运行缓存。重叠访问若包含普通put，须声明明确因果顺序；不同连接对同一位置的原子加法由实际接收事件顺序决定。配置层检查权限和越界，接收端仍根据报文重新检查，不相信发送端的工作说明。

这些事件用于功能驱动和离线核验：`applied`是目标内存模型已执行；`request_acked`是源收到请求字节的ACK；`completed`是应用队列可回收。目标事件可触发目标行为，不能把其全局记录当作现实源端已经知道的状态，也不能作为在线算法的额外观测。`applied`不代表GPU可见，`completed`不代表真实GIN flush或CQE格式。

## 根据实际收到的值推进下一轮

将运行命令的`--config`改为[local-waits.json](local-waits.json)，可以执行两轮目标端计数消费和通知。GPU0、GPU1向GPU4发送数据/计数；GPU4只有在本地收到的计数和数据**同时**符合条件时，才清零计数并通知两端进入下一轮。两个阶段共用一张网络、同一时钟和持久连接，不输入固定通信时刻。它是人工功能程序，不是MoE推理或现代DeepEP执行。

操作可选`local_wait`，含1—8个条件，全部满足后才提交该操作：

```json
"local_wait": [
  {"key": 7, "address": 4096, "compare": "eq", "value": 2, "reset_to": 0},
  {"key": 7, "address": 4112, "compare": "eq", "value": 11}
]
```

这里读取的是**本操作connection的src所拥有的窗口**，不是本操作的远端dst。没有可自选的owner字段，不能读取其他GPU的窗口。每项使用8B对齐地址和小端64位无符号整数，`eq`表示等于，`ge`表示大于等于；value/reset_to均在0—2⁶⁴−1内，同一列表不得重复位置。只有整个条件列表与`after`都满足时，才执行可选`reset_to`并提交到本地工作队列；等待条件期间不修改内存，进入队列也不代表已发布给NIC。

接收端状态仅由实际到达的MRT1字节、原子应用及明确的本地消费改变，条件不查发送端操作表。局部消费在同一个功能事件中完成，没有模型化GPU轮询时间、GPU内存序或部分DMA可见性；这个等待不能等同于现代GIN wait/flush。普通写入之间仍须满足原有显式因果约束，`local_wait`不会豁免这项检查。

`reset_to`本身不证明输入协议没有代际或生产/消费竞态；本例通过清零后的网络通知阻止下一轮提前写入，没有验证一般应用协议或源buffer寿命。

示例全部`after`只等待同一提交GPU已知的本地完成；目标端根据本地窗口推进，不借助全局`applied`事实。原接口保留全局事件作为功能编排能力，使用它们时仍须核对实际系统中谁能获得该事件。

`transactions.local_events`记录条件满足时实际读到的值、消费及提交GPU。它与`target_events`共用单调`order`，便于在同一ns内独立重放状态；order只在离线记录中，不写入报文。未能提交的操作在顶层`pending_operations`记录当前条件和值；到仿真截止仍不满足时，程序失败并保留结果，不补齐完成。

默认例完成10个事务、14个源数据包、560B载体有效负载；包含4个原子旧值返回，不含ACK及其他包头。两类反例分别改变原子增量和数据内容：二者均有7个后续操作等待，且不提前清零。重复结果逐字节一致，应用id改名不改变线上字节与时间。数字只说明本功能例，不能推断真实通信库成本；来源范围与复核见[等待功能验收](../../fixtures/local_memory_waits/README.md)。

## 实际传了什么

MRT1帧用网络字节序编码，32B头依次是magic=0x4d525431、verb、连接内事务序号、远端地址、远端key及body长度；字段宽度为4/4/8/8/4/4B。put body为实际字节，fetch-add body为8B增量，reply body为8B旧值且地址/key为0。事务序号负责请求/响应匹配，**应用operation id不写入报文**，不改变NIC绑定、优先级或选路。

接收端仅解析原生接收前缀新接受的包字节，允许帧跨包；它没有通过源端operation表推断写入内容。一个帧对应一次载体工作，包不跨工作边界；正常载体重复/乱序包不能推进接收前缀。独立内存核心另保留本连接生命周期内的完整请求与原子旧值，重复同一事务返回缓存结果、不再次加法；冲突重复和序号跳跃拒绝。此项已有核心检查，**没有实际网络丢包重试验收，也没有通用RTO或丢失原子响应后的恢复**。

默认2次put、4次原子，共6操作；MRT1请求256B、回复160B，总416B有效载荷、10个实际源包，另有原生ACK和各层包头。这里的40B原子请求和40B返回不是RoCE线上字节数。真实原子扩展头、应答头、ACK及ICRC不能由这个数字推导；原子响应在本模型作为普通反向字节流参与调度，真实硬件可能不同。

## 输出与完成

`result.json.transactions.operations`分开记录ready、published、applied、request_acked、response、completed，单位ns；未发生项为−1。原子另存returned_value。`windows`保存最终内存；`target_events`记录接收端实际解码及应用顺序，供离线核验；不提供给算法。`connections`保存队列前缀。

`transactions.transport`保存底层持久字节载体的状态。其`atomic_operations_supported=false`表示载体自身没有原子verb；原子功能由上层内存模型实现，不能理解为原生RoCE支持。`source_payload_packets`被动保存实际发出包的有效负载hex，结合source_packets的时间、端点、NIC和累计字节序号可独立还原帧。观测不参与执行。

所有应用操作完成后仍等返回值流的ACK、在途帧和物理队列排空，再关闭连接。默认最后应用完成60,370ns、物理排空66,394ns；只是这个人工功能模型的确定性结果，没有硬件含义或优化收益。

预算沿用原生协议仿真上限；宿主每例≤60秒、RSS1GiB、地址2GiB、输出64MiB、卷预留8GiB。至多64条载体工作（32请求加32回复），源包观测≤200,000项，捕获有效载荷≤2MiB。失败保留日志，不抬限重跑。输入JSON≤4MiB，功能实验不调用旧软件准入、恢复、故障、生命周期采集或机内bypass。

来源、独立字节核算、分包、并发、重复输入和边界见[验收与语义限制](../../fixtures/remote_transactions/README.md)。下一项必要能力是现代buffer/信号窗口与事件核心的在线映射，以及有根据的provider报文与可见性模型；本例不能直接接上现代计划就宣称已获得可信性能基线。
