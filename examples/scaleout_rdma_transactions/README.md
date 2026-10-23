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

## 等待调用时已有的工作前缀

将`--config`改为[queue-fences.json](queue-fences.json)，可以验证同一源GPU的两条连接如何完成已有工作，再发出一个标记。操作可选`"queue_fence": [0, 1]`，其中数字引用connections的id，含1—8个不重复且由本操作提交GPU拥有的连接。

当本操作的`after`满足时，运行时**只捕获一次**所选队列已经预留的工作数；这些前缀全部完成后才提交本操作。之后追加的工作不会扩大原快照。已预留但因额度不足而尚未发布的工作仍被纳入；空队列的目标是0，不会等待未来提交。

这个功能等待的是MRT1应用队列的ACK/原子返回完成，不是实际GIN flush、GPU源可复用或远端GPU可见。选中队列在一个功能回调中捕获；固定GDAKI源码按协作线程逐peer读取reserved索引，并非保证全局原子快照。现代应用接入必须保留生产者汇合及实际provider覆盖规则，不能用本例代替它们。

同一操作暂不允许同时配置`local_wait`和`queue_fence`，需要分成明确的应用步骤。输入数组在同一次progress中按顺序尝试提交，独立根工作不是一个隐含的生产者汇合：若将fence操作放在生产者之前且没有前驱，它会捕获空快照，标记可在数据之前发出。使用者必须按应用协议建立依赖。

`transactions.queue_fences`保存operation、owner、captured_ns、completed_ns及各连接的target/covered_operations。时间来自同一个网络时钟，未完成为−1。covered_operations仅用于离线核验，等待本身查询固定队列前缀，不用全局真值推断未来完成。快照捕获后尚未提交的操作仍列在顶层pending_operations；没有人为补齐完成。

默认例6个事务、52个源数据包、47,960B载体有效负载，第一次等待覆盖两个早期put，第二次覆盖新增工作。两次运行和id改名分别核验结果确定性及线上字节不变；另验证深度1下预留但未发布的工作、空快照和非本地连接拒绝。输入与全部功能边界见[队列快照验收](../../fixtures/queue_fences/README.md)。这些数字不用于真实通信库性能排序。

## 接收条件与本地处理交替执行

将运行命令的`--config`改为[local-stages.json](local-stages.json)，执行“收到数据和计数→本地处理→下一轮通知”。使用`moe-rdma-transactions/2`，根字段增加：

```json
"local_model": {"mode": "serial_per_gpu/1", "duration_source": "declared_ns"}
```

v2每个operations节点必须有`kind`。`remote`保留v1远端事务字段；`local`只含`id/kind/rank/duration_ns/after`和可选`local_wait`，例如：

```json
{"id": 10, "kind": "local", "rank": 4, "duration_ns": 40000,
 "after": [], "local_wait": [
   {"key": 7, "address": 4096, "compare": "eq", "value": 2, "reset_to": 0},
   {"key": 7, "address": 4112, "compare": "eq", "value": 11}
 ]}
```

`rank`须是拓扑中的GPU，编号0—31。`duration_ns`是0至协议`max_sim_ns`内的整数，默认上限5,000,000ns。它是用户声明的处理时间，没有从GPU型号、算子或batch自动估计。每GPU一个非抢占串行槽，不同GPU可以并行；零时长也经过ns-3调度事件。v2至多32个远端事务和16个本地节点，至少一个远端事务。这是有界功能入口，不能据此声称支持千卡MoE执行。

本地节点的`after`引用数组中的较早节点：远端前驱可等applied/request_acked/completed，本地前驱只能等completed。它没有connection/verb/queue_fence或绝对ready时间。`local_wait`只读本节点rank的实际接收窗口。

前驱和全部窗口条件满足时，节点**入队时就消费/reset条件**，不等开始执行。排队期间不占网络工作额度。已排队节点先于刚解锁节点执行；处理实际完成后，后继远端事务才可提交。QP、拥塞状态和整个网络保持在同一时钟中，本地尾部未完成时不提前关闭连接。

入队语义不锁定输入内存，也不保留buffer租约。生产者若提前覆盖数据，模型不能证明处理读到的数值正确；应用须建立复用协议。applied及条件满足不等于GPU可见，声明处理时间不能当作实测CUDA成本。示例部分applied依赖仅用于功能编排，不能直接当作现实源GPU可获得的通知。

v2输出增加`local_model/local_work/completed_local_work/pending_local_work`四个顶层字段。`local_work`含id/rank/duration_ns/ready_ns/start_ns/completed_ns；ready表示入队、start表示取得本地槽、completed表示实际定时事件已执行，未发生为−1。`completed_operations`仍只数远端事务；`pending_operations`只列未提交的远端事务，已提交未完成须查看transactions.operations。`pending_local_work`含id/admitted/当前local_wait，已入队节点可能已消费清零，不能据当前条件值推断从未就绪。

默认人工例完成10个远端事务、6个本地节点，发出14个源数据包/560B载体负载。错误计数或数据均停在3个已完成事务，7个后继不发送；5ms截止保留已消费但仍排队的节点。旧v1逐字节对照及独立包/内存重放见[验收](../../fixtures/transaction_local_stages/README.md)。这些是功能检查，没有执行LLMServingSim、现代GIN或GPU校准。

## 将实际接收内容作为后续发送的数据

将运行命令的`--config`改为[source-windows.json](source-windows.json)。该人工例在同一网络中执行GPU0→GPU4→GPU1→GPU0，后两跳读取本次实际收到的窗口，而非从任务文件复制预设内容。使用`moe-rdma-transactions/3`，保留v2的`kind/local_model`，根增加：

```json
"source_model": {
  "capture": "registered_window_at_publish/1",
  "release": "transaction_completed/1"
}
```

远端put只选一种负载：原有`hex`或`"source": {"key": 8, "address": 8192, "bytes": 257}`。source属于本连接的src，不能指定owner；范围须完整处于其注册窗口，长度1—32768B。原子操作不能指定source，source也不能与本操作local_wait的reset范围重叠，需要拆成明确步骤。

提交put时取得源范围的读使用记录（lease），即使因队列深度不足而尚未发布，也保持保护。真正发布时从当前注册窗口快照实际字节，编码进MRT1。直到本事务请求ACK并按序完成才释放；多个重叠读可共存，所有读完成后才能覆写。普通远端写、原子写和本地reset均不能覆盖活跃源范围；多个reset先全部检查再执行，避免只消费一半条件。

此处只实现**发布时快照和MRT1完成保护**，没有逐包GPU读取、真实CQ或source-safe成本。本地处理节点本身尚无数值输入和输入保护；若在本地处理期间、后继put提交之前合法覆写窗口，后继会发送新内容。它不能证明处理读到了哪一代输入，应用接入仍需定义计算读写与复用协议。

v3的`transactions`增加`source_model/source_events/active_source_leases`。source_events含operation、owner/key/address/bytes、order/time_ns、kind（acquired/captured/released）；captured另含实际hex。order与接收应用和本地消费共用，便于离线重放，不参与报文或选路。运行期冲突退出1，保留`passed=false`、`execution_error`和实际部分结果；截止保留未完成lease，不伪造释放。输入验证失败仍不生成结果，v1/v2字段保持。

人工例核验6个远端事务、3个本地阶段，27个源数据包、1,525B载体负载。只修改初始窗口即可改变全部后续转发内容；同源排队保护、三类覆写拒绝、提交前覆写对照、截止、旧版逐字节比较及独立实包重放见[源窗口验收](../../fixtures/source_windows/README.md)。这些结果不表示现代MoE性能或优化收益。

## 本地处理显式读取输入和写出输出

将`--config`改为[local-io.json](local-io.json)，可以执行“实际接收→本地复制到另一地址→后继put读取输出”。使用`moe-rdma-transactions/4`，沿用v3字段，根增加：

```json
"local_memory_model": {
  "mode": "guarded_copy/1", "acquire": "admission/1",
  "capture": "start/1", "commit": "completion/1"
}
```

local节点可选`"copy": {"source": {"key": 8, "address": 8192, "bytes": 257}, "target": {"key": 8, "address": 8576}}`。两个范围都属于本节点rank，target长度等于source长度（1—32768B），须完整处于注册窗口。源目标和本节点reset均不得重叠，不接受额外owner、target bytes或其他字段。没有copy的本地节点仍只有声明耗时，不自动取得任何数据保护。

全部条件满足后先检查源可读、目标可独占写、全部reset可写，再消费条件并入队。源读和目标写的使用记录从**入队**持续到完成，包括等待本地串行槽的时间。多个源读允许重叠；输出区在此期间不能被其他源读、LE64条件读、put、原子或reset访问，其他写也不能覆盖输入。冲突退出1并保留部分状态，不把功能错误换算成硬件等待。

真正开始时读取当前源字节，声明处理时间到期后一次写入目标，再释放两个记录并标记完成。零时长也经过事件调度。后继put由本地completed依赖放行，读取这个实际输出；完成后输入、输出可以按应用依赖合法复用。该模型只做**字节原样复制**，处理时长仍是声明值，不是专家矩阵计算、CUDA DMA或内存带宽模型，保护区间也是显式功能规则，不能作为真实GPU时延依据。

v4的transactions新增`local_memory_model/local_memory_events/active_local_memory_leases`。每项事件含operation、order、time_ns、kind及source/target范围；kind依次为acquired/captured/committed/released，capture和commit另含实际hex。所有内存事件共用order，原有source_events只记录远端put的源保护。截止保留已取得而未释放的本地记录，未完成就不写输出。若失败后的当前条件因输出独占而不可读，pending项保存`local_wait=[]`及`local_wait_error`，不会用此空列表表示条件已满足，也不让报告读取再次失败而丢掉原始错误。

人工例完成7个远端事务和4个本地节点，其中2个声明了copy；实包、排队/运行期冲突、源共享、截止、新旧比较和恢复范围见[本地输入输出验收](../../fixtures/local_io/README.md)。该规则补齐了这个显式复制程序的输入输出保护，未声明copy的节点以及完整现代MoE仍不具备这项保证。

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

## 在线追加工作与实际回调

`rdma-transaction-session`让前端根据本次实际接收、ACK、原子回复和本地完成继续提交事务。它使用同一原生网络时钟与持续存在的QP；前端计算/等待所花的宿主时间不计入仿真时钟。MRT1规则和v4范围保持，不是现代GIN、GPU可见性或硬件校准。

默认前端读取普通v4程序，但完整程序只保留在Python侧；后端启动文件`initial.json`的operations为空。只有`after`中的真实事件全部发生后，前端才递交对应节点。节点自身的`local_wait`、队列前缀和资源准入继续由原生后端检查。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-transaction-session \
  --topology /ABS/TOPOLOGY_DIR --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/session-program.json \
  --backend /ABS/ns3.42-moe-rdma-transaction-session --output /dev/shm/NEW_SESSION
```

后端由`rdma-build`的`transaction_session`字段返回，使用方式与已有probe相同。所有产物目录必须新建，tmpfs预留8GiB；公开命令限制60秒、RSS1GiB/地址2GiB、64MiB产物，仿真默认5ms、结果16MiB，不自动扩限。

需要根据真实返回值构造后继时，使用`moe_ub_cosim.transaction_session.NativeTransactionSession`。`receive()`返回当前DECIDE（或成功结束时的None），`commit(nodes, finish=False)`只能回应这一决策。初始化传v4对象且operations为空；每次提交保留历史ID及依赖，累积32远端/16本地、8连接/16窗口，不能复用ID。调用者不得发送完成通知或未来就绪时刻。自定义驱动必须整体放在公开`host_probe`下；客户端另有60秒宿主截止，异常关闭仅回收其自己启动的子进程。

可复制的[驱动示例](session_driver.py)读取[两节点程序](session-program.json)，先发原子加3，取得实际旧值后再生成“旧值+100”的小端写数据。把窗口初值从7改成19，第二次实际写入会从107变成119，无需修改后端或预先填结果：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/NEW_SESSION_MONITOR --wall-seconds 60 \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --artifact-root /dev/shm/NEW_SESSION --min-free-bytes 8589934592 -- \
  python3 examples/scaleout_rdma_transactions/session_driver.py \
  --topology /ABS/TOPOLOGY_DIR --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/session-program.json \
  --backend /ABS/ns3.42-moe-rdma-transaction-session --output /dev/shm/NEW_SESSION
```

`native.stdout.log`与`control.jsonl`逐完整行保存；`native-command.json`登记原生命令、cwd和输入hash。最终result增加`session`的注册时刻、实际事件及决策数；包、内存与v4本地记录仍按原格式保存。命令最多1MiB，256次决策；整体命令的字段/范围/依赖校验失败不会执行同批有效前部，运行期内存冲突则保留已发生的实际部分状态，不回滚网络。

| 回调 | 功能含义 |
|---|---|
| submitted / published | 应用依赖满足后提交 / 已有队列信用且发布实际请求 |
| applied | 目标端从真实请求字节执行put或原子；不等于GPU visible |
| request_acked | 原生请求字节累计ACK到达源 |
| response | 实际原子旧值回复到达源；附returned_value |
| completed | MRT1本地完成（请求ACK与必要回复、前序完成），或本地节点真实完成 |
| local_admitted / local_started | 本地条件消费并入队 / 占用该GPU的声明处理槽 |

会话的`drained=true`表示当前已注册工作、本地槽与物理传输均空闲，QP仍保留；前端可以继续提交下一轮。只有显式`finish=true`才关闭持久连接，此后最终Fabric的`drained`还验证QP资源已清空。EOF、错误决策或实际截止不是成功；运行期错误保留partial result，宿主强制终止可能只留下已刷出的前缀和监控记录。self-peer仍拒绝，不能补成零成本或虚构外部路径。现代事件核心尚未接入，验收边界见[在线会话验收](../../fixtures/transaction_session/README.md)。
