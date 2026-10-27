# 远端地址与原子返回值的功能小例

可选容量模型及公开例见[有限副本与实际ACK回收](#有限副本与实际ack回收)。

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

## 配置较多连接与较长程序

v5可选以下完整声明；省略时仍为8条连接、32个远端操作、16个本地节点：

```json
"execution_limits": {
  "max_connections": 12,
  "max_remote_operations": 96,
  "max_local_operations": 20
}
```

三个字段均须为整数，范围分别是1—64、1—1024和0—256；连接id小于声明连接上限。它们限制整次运行的累计数量，在线会话按所有已接受批次累计，操作完成不会清零额度。超额候选整批拒绝，可继续提交合法工作；不能在会话中修改上限。

载体上限由连接数和远端操作数分别派生为请求/回复的两倍；源读保护记录按远端＋本地总节点数限制，窗口和源数据的保护规则仍生效。队列深度仍为1—32的2次幂，GPU编号仍为0—31，窗口仍最多16个，本地输出的同时写保护仍最多16项，字节、观察及宿主预算独立检查。声明的执行额度是功能容量，不能解释为NIC或GPU硬件容量，也不能据此宣称千卡应用已可执行。

[execution-limits.json](execution-limits.json)在GPU标签0、1、4、5间建立12条有向连接，实际执行96次写入和20次依赖实际完成的本地复制。沿用上面的topology与protocol，把`--config`换成此文件即可执行；在线会话也接受同一配置。声明时长10ns只是功能输入。输出只在显式配置时增加`transactions.execution_limits`，旧输入不增加该字段。功能证据和失败记录见[验收](../../fixtures/transaction_limits/README.md)。

这些事件用于功能驱动和离线核验：`applied`是目标内存模型已执行；`request_acked`是源收到请求字节的ACK；`completed`是应用队列可回收。目标事件可触发目标行为，不能把其全局记录当作现实源端已经知道的状态，也不能作为在线算法的额外观测。`applied`不代表GPU可见，`completed`不代表真实GIN flush或CQE格式。

## 选择重传下执行实际事务

v5可选`dcqcn_reference`，字段与[独立拥塞反馈参考](../scaleout_rdma_streams/README.md#可选的独立拥塞反馈参考模式)一致；它在创建连接前配置，也可与`go_back_n/1`配合，便于保持相同拥塞控制作恢复对照。选择`transport_recovery.mode="selective_repeat/1"`必须同时显式提供该参考。v1—v4拒绝新配置，`buffer_model`有界副本池与本组合不兼容；不能省略参考或悄悄退回另一种控制器。

完整输入见[selective-chain.json](selective-chain.json)。沿用上文topology、protocol和执行命令，把`--config`换成此文件即可。它依次执行2600B写入、原子计数、本地等待/清零与复制，再通过第二条连接转发，源端等实际完成后另作复制；输入是人工功能链。在线会话接受相同静态字段，后续任务仍由实际回调追加。

选择模式额外声明`receive_buffer_bytes`和`inflight_bytes`（均为MTU—2MiB），接收容量至少为窗口＋MTU−1；`max_events`必须为正数并覆盖所有选择记录。接收端保留真实乱序包，只有缺口补齐后的连续字节才按序交给事务解码器；重复包不重复交付。选择ACK不会执行写入或原子加法，也不释放源工作额度，源仍等累计ACK及原子实际回复，按连接顺序完成。

选择记录保存在`transactions.transport.selective_repeat`，`payload_delivered`包含实际包UID、序号、字节hex和交付时刻；它和其他选择事件共用事件预算。目标操作仍以完整MRT1请求为单位执行，2600B写入跨包到齐前不应用。目标应用、请求ACK、原子响应和源完成分别记录，不能混为GPU可见或硬件CQ事件。

[独立验收](../../fixtures/transaction_selective_payload/README.md)覆盖首请求丢失、末尾回复丢失、跨包写/复制/转发、显式发布和在线按返回值构造后续任务。它核对实际包、反馈、字节交付、内存及源保护；这是强恢复基线进入事务链的功能条件，不是新的恢复算法或性能收益证据。MRT1/MSR1仍为模拟格式，没有新增现代GIN、自发QP物理路径、GPU可见或硬件校准。

## 多NIC的权限共享同一份存储

v4/v5可显式同时加入`registration_model={"mode":"nic_scoped_aliases/1"}`与`registrations`。此时`windows.key`是本地存储身份，远端操作的`key`是rkey（远端访问密钥），由目标owner、实际绑定的接收NIC及rkey共同查找；未命中直接拒绝。本地source、copy和local_wait仍使用存储key。省略两字段时保持原有行为。

每项注册包含`id/owner/nic/key/window_key/address/bytes/allow_atomic`。共1—32项，id和owner/NIC/key分别唯一；NIC须出现在连接端点绑定中。`window_key`引用同owner的存储，注册仅能授予其子范围并收紧原子权限。同rkey可在不同NIC使用；不同rkey可指向相同字节。物理窗口仍不重叠且只分配一次，多个注册不复制数据；更换rkey不能绕过重叠写的因果顺序或源读、本地写保护。

[nic-registration.json](nic-registration.json)有两个64B物理窗口、四项注册、两个不同NIC的正向连接和一个返回连接。它先写两段数据，再经独立原子权限加一，随后把实际修改后的内容发回，最后本地复制。以下命令使用已有原生程序；输出目录均须不存在：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma_transactions/nic-registration-topology.json \
  --output runs/nic-topology
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/nic-topology \
  --protocol examples/scaleout_rdma_transactions/nic-registration-rdma.json \
  --config examples/scaleout_rdma_transactions/nic-registration.json \
  --backend build/native-transactions/output/moe-rdma/ns3.42-moe-rdma-transaction-probe \
  --output runs/nic-result
python3 -B fixtures/nic_registration/audit.py \
  --result runs/nic-result/result.json \
  --work examples/scaleout_rdma_transactions/nic-registration.json \
  --output runs/nic-audit.json
```

也可用`rdma-transaction-session`和对应session二进制运行同一配置。示例使用两plane各100Gbps接入的NIC1/2（loopback为0）；不是给单端口额外增加带宽。独立核验按捕获的真实入站端口解码MRT1、重放存储并检查返回值，范围见[功能验收](../../fixtures/nic_registration/README.md)。这是静态功能权限，不模拟Protection Domain（保护域）、动态撤销、逐句柄退出、DMA在途寿命或注册成本，不能据此判定失败窗口可安全复用或计算恢复性能。

## 应用窗口关闭与底层注册分开

v4/v5可选成对的`application_window_model={"mode":"local_views/1"}`与`application_windows`，要求已有NIC静态注册。每个视图为`id/owner/window_key/address/bytes`，1—32个、id唯一、范围在物理窗口内。它们是输入声明的本地应用引用，共享底层字节和已有注册，不执行真实注册协商或创建新MR。

操作的`application_uses`列出1—8个本GPU的开放视图。启用模式后，source、copy两端和local_wait的每个本地范围必须由其中某一视图完整覆盖。实际准入时开始使用，实际操作completed时结束；prepared、目标applied和单独ACK都不能代替完成。纯local节点的`release_window: id`在声明duration结束时释放一个视图，不能同时含copy/wait/uses。静态检查要求关闭依赖此前使用的completed（可传递），并保守拒绝所有在已声明关闭后添加的使用；数组顺序本身不证明完成。运行时还会检查开放状态和活跃使用。

[application-windows.json](application-windows.json)依次关闭第一个目标视图、经第二个视图复制8B、关闭第二个视图，然后发送方经自己的视图发包，最后关闭源视图。示例用已有任务依赖编排功能反例；不是分布式故障检测算法，发送准入不读取目标视图状态。先生成上节NIC拓扑，再执行：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/nic-topology \
  --protocol examples/scaleout_rdma_transactions/nic-registration-rdma.json \
  --config examples/scaleout_rdma_transactions/application-windows.json \
  --backend build/native-transactions/output/moe-rdma/ns3.42-moe-rdma-transaction-probe \
  --output runs/application-windows
python3 -B fixtures/application_windows/audit.py \
  --result runs/application-windows/result.json \
  --work examples/scaleout_rdma_transactions/application-windows.json \
  --output runs/application-windows-audit.json
```

也可换用session命令及对应二进制；在线完整候选在改变运行状态前检查。事务结果`application_windows`分别报告视图、共享底层域与acquire/release/close事件。最后应用引用为0时状态为`no_application_refs_provider_pending`，不是注销完成或安全复用：NIC/rkey仍有效，实际到达的数据继续修改物理字节。没有模拟MR退出、DMA、CUDA可见性或真实成本；不能据此宣称生产漏洞或恢复性能。验收见[范围与证据](../../fixtures/application_windows/README.md)。

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

接收端仅解析原生接收前缀新接受的包字节，允许帧跨包；它没有通过源端operation表推断写入内容。一个帧对应一次载体工作，包不跨工作边界；正常载体重复/乱序包不能推进接收前缀。独立内存核心另保留本连接生命周期内的完整请求与原子旧值，重复同一事务返回缓存结果、不再次加法；冲突重复和序号跳跃拒绝。显式`transport_recovery`已支持实际丢包、重复数据ACK与原子响应恢复，独立核对实际字节和单次副作用，见下文“持久事务的丢包恢复”；默认未开启时仍没有通用超时重传。

默认2次put、4次原子，共6操作；MRT1请求256B、回复160B，总416B有效载荷、10个实际源包，另有原生ACK和各层包头。这里的40B原子请求和40B返回不是RoCE线上字节数。真实原子扩展头、应答头、ACK及ICRC不能由这个数字推导；原子响应在本模型作为普通反向字节流参与调度，真实硬件可能不同。

## 输出与完成

`result.json.transactions.operations`分开记录ready、published、applied、request_acked、response、completed，单位ns；未发生项为−1。原子另存returned_value。`windows`保存最终内存；`target_events`记录接收端实际解码及应用顺序，供离线核验；不提供给算法。`connections`保存队列前缀。

`transactions.transport`保存底层持久字节载体的状态。其`atomic_operations_supported=false`表示载体自身没有原子verb；原子功能由上层内存模型实现，不能理解为原生RoCE支持。`source_payload_packets`被动保存实际发出包的有效负载hex，结合source_packets的时间、端点、NIC和累计字节序号可独立还原帧。观测不参与执行。

所有应用操作完成后仍等返回值流的ACK、在途帧和物理队列排空，再关闭连接。默认最后应用完成60,370ns、物理排空66,394ns；只是这个人工功能模型的确定性结果，没有硬件含义或优化收益。

预算沿用原生协议仿真上限；宿主每例≤60秒、RSS1GiB、地址2GiB、输出64MiB、卷预留8GiB。至多64条载体工作（32请求加32回复），源包观测≤200,000项，捕获有效载荷≤2MiB。失败保留日志，不抬限重跑。输入JSON≤4MiB，功能实验不调用旧软件准入、恢复、故障、生命周期采集或机内bypass。

来源、独立字节核算、分包、并发、重复输入和边界见[验收与语义限制](../../fixtures/remote_transactions/README.md)。下一项必要能力是现代buffer/信号窗口与事件核心的在线映射，以及有根据的provider报文与可见性模型；本例不能直接接上现代计划就宣称已获得可信性能基线。

## 在线追加工作与实际回调

公开`rdma-transaction-session`命令可增加`--reuse-static-validation`，Python类对应`reuse_static_validation=True`；默认关闭。仅复用本会话内类型与内容完全匹配的拓扑/协议/派生传输声明检查，所有动态任务和原生准入完整执行。退出时的`static-validation.json`仅记录宿主校验次数，字段与边界见[固定校验复用](../modern_deepep_native/README.md#可选的固定校验复用)。不改变通信结果，也不代表已测得加速。

`rdma-transaction-session`让前端根据本次实际接收、ACK、原子回复和本地完成继续提交事务。它使用同一原生网络时钟与持续存在的QP；前端计算/等待所花的宿主时间不计入仿真时钟。MRT1规则和v4范围保持，不是现代GIN、GPU可见性或硬件校准。

默认前端读取普通v4程序，但完整程序只保留在Python侧；后端启动文件`initial.json`的operations为空。只有`after`中的真实事件全部发生后，前端才递交对应节点。节点自身的`local_wait`、队列前缀和资源准入继续由原生后端检查。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-transaction-session \
  --topology /ABS/TOPOLOGY_DIR --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/session-program.json \
  --backend /ABS/ns3.42-moe-rdma-transaction-session --output /dev/shm/NEW_SESSION
```

后端由`rdma-build`的`transaction_session`字段返回，使用方式与已有probe相同。所有产物目录必须新建，tmpfs预留8GiB；公开命令限制60秒、RSS1GiB/地址2GiB、64MiB产物，仿真默认5ms、结果16MiB，不自动扩限。

需要根据真实返回值构造后继时，使用`moe_ub_cosim.transaction_session.NativeTransactionSession`。`receive()`返回当前DECIDE（或成功结束时的None），`commit(nodes, finish=False)`只能回应这一决策。初始化传v4/v5对象且operations为空；每次提交保留历史ID及依赖，累积32远端/16本地、8连接/16窗口，不能复用ID。调用者不得发送完成通知或未来就绪时刻。自定义驱动必须整体放在公开`host_probe`下；客户端另有60秒宿主截止，异常关闭仅回收其自己启动的子进程。

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

## 不占用GPU队列的控制定时器

业务重试期限或控制器定时检查应使用在线API的`alarms`，不要表示成占用`serial_per_gpu/1`槽的本地计算。它仍由同一个原生ns-3时钟推进，只唤醒前端，不触发网络完成、GPU完成或远端通知：

```python
session.commit(nodes, alarms=[{"id": 7, "delay_ns": 100000}])
# 原生时间真正到达截止后，receive()的events中出现：
# {"alarm": 7, "event": "fired", "time_ns": 实际截止时刻}
# 条件提前满足时，可以显式取消仍pending的timer：
session.commit([], cancel_alarms=[7])
```

`delay_ns`相对当前DECIDE的`now_ns`，可为0，但截止必须严格早于本次`max_sim_ns`。ID为0—65535，整个会话累计最多32个，已触发/取消的ID不能复用；alarm ID与operation ID分开。不能把alarm ID放入操作的`after`依赖：前端收到真实fired回调后，再递交需要的工作。同纳秒允许多个回调，只依赖其事件集合与时刻，不依赖独立事件的排列顺序。

`DECIDE.drained=true`只表明当前工作和物理网络空闲，可能仍有控制timer。只剩timer时可`commit([])`等待；`finish=True`要求控制timer也已触发或被本批显式取消，不会自动取消。若取消最后timer且没有其他可推进工作，应在同批明确`finish=True`，或者提交新工作/timer，不能无限空等。未知、重复、已触发或已取消ID的取消请求会失败；同批新建再取消同一ID也拒绝。操作与timer整批校验通过前，不执行有效前部。

[可复制驱动](control_alarm_driver.py)使用[小型输入](control-alarm.json)：先发送8B，按实际接收复制，再取消原定100μs控制期限。原输入中的本地copy仍遵守GPU FIFO；控制timer不占这个槽。运行整个驱动：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/NEW_ALARM_MONITOR \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 67108864 --wall-seconds 60 \
  --artifact-root /dev/shm/NEW_ALARM_SESSION --min-free-bytes 8589934592 \
  -- python3 -B examples/scaleout_rdma_transactions/control_alarm_driver.py \
  --topology /dev/shm/SMALL_TOPO --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/control-alarm.json \
  --backend /ABS/ns3.42-moe-rdma-transaction-session --output /dev/shm/NEW_ALARM_SESSION
```

timer不产生控制报文，没有声明CPU处理成本，也不模拟真实故障检测器；需要跨端通知时仍须显式建模通信。本例的更早复制是输入语义修正，不能当恢复算法加速。故障源仍可能未确认、持有源缓冲，不能因为控制期限结束或结果已收到而释放。完整范围见[平台验收](../../fixtures/session_control_alarms/README.md)。

## 使用大地址范围与稀疏初值

需要保留现代布局的高偏移地址时，使用`moe-rdma-transactions/5`和[稀疏小例](sparse-windows.json)。v5保留v4所有根字段和节点规则，只把每个窗口的完整`hex`改为逻辑长度与初始段：

```json
{
  "owner": 0, "key": 7, "address": 4294967296, "allow_atomic": true,
  "bytes": 18874368, "initialization": "zero_fill/1",
  "segments": [{"offset": 17008640, "hex": "01020304"}]
}
```

`address`和操作地址是同一模拟地址空间的绝对字节地址；`offset`相对本窗口。全部未给出的初值由**输入显式声明为零**，不表示未知模型张量为零。窗口逻辑长度1—64MiB，初值至多32个不重叠段、合计64KiB；零段列表允许。实际写入以4KiB页保存，每窗口最多256页/1MiB，含初始化页。零读取不分配页，写零也分配，已分配页在本次运行中不回收。逻辑长度、地址越界、所有key权限及读/写使用记录继续检查；页容量不足时整个本次写拒绝，不部分写入或推进事务序号。

使用上面的普通probe或在线session命令，把`--config`换成`examples/scaleout_rdma_transactions/sparse-windows.json`即可；需要本次更新构建的对应入口。此例在三个18MiB窗口内实际发送14,464B、复制并转发，同时执行小型原子与条件清零。窗口长度、staging偏移和数据长度对应声明的EP4/H7168/Top8布局；数据是功能模式，处理时长声明为零，**不是完整MoE、专家计算或真实GPU性能**。不能据此比较部署时延，self及可见性限制保持。

v5结果窗口不含展开的`hex`，改为`bytes`、`initialization`、`resident_bytes`及按offset升序的`pages: [{offset, hex}]`。每页保存4096B，最后一页按逻辑末端截短；未列出的页读零。`resident_bytes`只统计本仿真器的页字节，不含索引/容器/事务缓存，更不是GPU占用、进程RSS或网络流量。其他实际包、队列、内存事件及完成字段沿用v4。结果仍受16MiB预算约束，输入允许的最大组合不保证输出预算内成功；超限保留监控前缀，不自动增限。

原生事务和在线入口均支持v5；v1—4继续使用原窗口格式，不自动转换。独立字节重放、稠密差分、页限失败不产生部分写、原生非法声明拒绝及恢复见[稀疏窗口验收](../../fixtures/sparse_windows/README.md)。

## 准备工作后统一发布

需要区分“描述符已准备”和“NIC可以开始消费”时，使用v5的[前缀发布小例](submission-prefix.json)。在上述probe或session命令中替换`--config`即可，拓扑、协议和预算沿用原命令。根字段增加：

```json
"submission_model": {"mode": "explicit_prefix/1"}
```

启用后，每个`remote`节点必须声明布尔`aggregate_requests`：`true`只准备，不发布；`false`准备自己后，发布**同一connection中到自己为止的已准备前缀**。它不合并put/原子请求，不发布其他连接，不创建新QP，也不改变ACK或原子返回的完成要求。未选此模式时不能填写该字段，v1—4及v5默认行为保持。

远端前驱可以使用`after: [{"operation": 0, "event": "prepared"}]`。该事件由实际队列额度可用并完成准备时产生，session也会把它反馈给前端；提交成功不代表已经准备。源窗口仍从提交开始保护，真正发布时才捕获字节，完成后释放。示例先准备put，再由prepared回调启动声明为20μs的本地工作，最后非聚合的原子请求发布两项工作；20μs前同连接没有数据包。这是人工功能程序，没有测量门铃或GPU成本。

`prepared`不能证明旧写已到达目标。两条连接要重复写入同一地址范围，须通过`applied`、`request_acked`或`completed`建立远端副作用顺序；夹入一个本地等待节点也不能把准备事件变成完成证据。源端程序优先使用自身实际收到的ACK或完成，不能查询远端`applied`真值。若旧请求仍聚合而未发布，必须先安排显式发布；等待ACK不会自行发出请求。同一连接中，已准备前驱与其因果后继沿同一有序字节流执行；该后继的真实完成也覆盖这些前驱。独立输入数组位置不证明提交顺序。Python、原生事务及在线追加都会拒绝缺少上述顺序的重叠非原子写；不同地址范围与允许的原子并发继续适用。

队列槽不足时，后续工作可能尚未准备；若前面的聚合工作一直没有发布，整个程序会在真实仿真截止时保留未完成状态。最后一项也设`true`不会自动补发布。`queue_fence`只等待调用时已预留的前缀，也不会替应用发布；使用时须显式安排一个可以获得槽的非聚合工作。不能通过自动加深队列或补完成掩盖阻塞。

启用模式的`transactions.operations`增加`prepared_ns`，未发生为−1；`transactions.submission`保存`mode`和`publications`，每次含`connection/operation/exclusive/time_ns`。exclusive是从0开始的队列ticket的排他上界，表示逻辑前缀，不是网络包数或物理MMIO门铃次数。同一纳秒的两次显式发布仍是两条记录。prepared、published、目标applied、request_acked、response和completed分别保留，均不能直接等同GPU可见。

此模型依据固定provider源码中的跳过门铃语义，只验证显式发布边界；不模拟所有GIN flush、CPU代理或硬件信用优化。持久事务仍拒绝旧式recovery/drop_once配置；faults仅在显式transport_recovery时启用，见下节。独立包核验、阻塞反例、在线回调、默认行为差分和恢复见[验收记录](../../fixtures/submission_prefix/README.md)。

### 持久事务的丢包恢复

事务v1—v5及在线v4/v5初始配置可增加`transport_recovery`，字段、累计序号和失败处理见[持久恢复](../scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)。同一连接的请求stream为`connection*2`、响应stream为`connection*2+1`；协议faults中的message_id指这个stream，不能填operation ID。真实重传的重复字节不重新进入事务解析器，原子响应也经真实重传到达源端。request_acked、applied、response和completed继续独立记录；源保护/事务信用仍由已有完成条件释放。恢复状态位于`transactions.transport.recovery`，不是顶层persistent_transport。没有加入该字段时默认保持。

## 有限副本与实际ACK回收

[bounded-buffers.json](bounded-buffers.json)使用transactions/4，把固定注册窗口、发送重传副本、目标去重缓存和本地copy快照分别计量。三个8B put都在起点就绪，但每源40B副本池只容纳一个带32B MRT1帧头的请求；只有实际完整ACK到达后才释放副本、允许捕获下一次源数据。最后按实际应用事件复制目标窗口。它可用于检查有限容量、丢包与恢复的相互影响。

在已有构建中选择本版本transaction-probe可执行文件，使用新的输出目录：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/NEW_BUFFER_TOPOLOGY
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology /dev/shm/NEW_BUFFER_TOPOLOGY --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/bounded-buffers.json \
  --backend /ABS/ns3.42-moe-rdma-transaction-probe --output /dev/shm/NEW_BUFFER_RUN
```

在线`rdma-transaction-session`也支持相同配置。`buffer_model`必填以下5个字段，可选源隔离回收策略见下节：

```json
{"mode":"bounded_copied_put/1","transport_bytes_per_source":40,
 "registered_bytes_per_owner":256,"target_cache_bytes_per_owner":120,
 "local_copy_bytes_per_owner":8}
```

| 字段 | 内容和作用范围 | 接受范围（B） |
|---|---|---|
| transport_bytes_per_source | 同一源GPU全部连接尚未完整ACK的MRT1副本，包含32B功能帧头 | 33—2,097,152 |
| registered_bytes_per_owner | 每owner的固定窗口内容合计 | 1—1,048,576 |
| target_cache_bytes_per_owner | 默认每目标接纳的全部请求帧累计预留；显式carrier-once put不保留完整缓存 | 默认33—2,097,152；carrier-once必须0 |
| local_copy_bytes_per_owner | 每GPU串行本地copy槽中的快照内容 | 1—32,768 |

整数拒绝bool、浮点、负值和额外字段；每个请求必须放得进源池。当前只支持v4的注册源put，拒绝inline put、原子、v5和explicit-prefix组合。注册/目标缓存总额在完整候选中先检查；在线超额批次不会注册其有效前部，也不会取消已有timer。

不同源的池独立，同源连接共享容量。池不足时不会提前读取源窗口，也不丢弃工作；完整ACK清空真实传输副本，唤醒同源等待连接。默认情况下，超时、重试、目标已应用和控制timer均不释放失败副本；源lease仍按原`transaction_completed/1`释放。目标请求缓存不随ACK清除，也没有未建模的安全淘汰：长期服务会受累计接纳量限制，不能称无限连续运行。

`result.json.transactions.buffer_retention`分别列出注册内容、目标缓存、源lease引用量、本地copy当前/峰值和捕获临时向量峰值；编码后不再长期保留operation body，本地copy实际完成后清空快照。`transactions.transport.payload_pool`记录每源当前/峰值、未释放持有者及每次acquired/released的实际时间。失败partial结果可能仍持有资源，这是未完成的实际状态。

这些额度只覆盖所列数据内容；元数据、解码临时对象、包、观测hex和报告副本不在内，源lease可重叠且只是引用，不能将字段相加为总RSS或GPU显存。MRT1帧头不是RoCE开销，ACK回收不提供远端撤权、GPU可见性或现代provider保证。功能验收、故障注入更正和恢复证据见[有界缓冲验收](../../fixtures/bounded_buffers/README.md)。

## 失败连接停止发包后归还复制池

一条失败连接若一直占满同GPU的共享复制池，其他健康连接也不能发布。当前载体先复制源字节，再复制到原生包；源端隔离后不再读取本地重传副本。因此可在`buffer_model`显式增加：

```json
"on_source_isolation": "release_copied_payload/1"
```

同时必须配置`transport_recovery.on_exhausted="isolate_stream"`。只适用于既有v4复制型put，拒绝布尔值、其他策略或全局停止模式；省略时行为和结果保持。使用新构建`backend.json`中的`transaction_probe`和上文8GPU小Clos拓扑：

```bash
python -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology /path/to/topology \
  --protocol examples/scaleout_rdma_transactions/rdma-isolated-copy-release.json \
  --config examples/scaleout_rdma_transactions/isolated-copy-release.json \
  --backend /path/to/transaction-probe --output /dev/shm/isolated-copy-release
```

本例持续丢弃连接0请求的最终ACK，预期5ms截止退出1并保存部分结果，不能称整体恢复完成。GPU0共享池只容40B帧：旧连接在60μs隔离并归还副本，健康连接的操作2、3分别在72.060/84.120μs完成。失败连接的后续操作4保持未发布、不重新捕获源字节。这些是功能输入的结果，不是GPU/生产时延。

池事件`discarded_on_source_isolation`记录实际丢弃副本，`payload_pool.discarded_work`列出stream/work/bytes/time_ns；新增`on_source_isolation`说明策略。只在源移出调度并取消源计时器后丢弃。它不产生ACK或队列完成，不释放应用源lease、目标窗口或去重缓存；`failed_resources_released=false`仍为真。已构造原生包有独立字节，仍可能在回收后到达并写目标，不能据此复用旧目标地址。

将协议换为普通`rdma.json`且换新输出目录，可查看无故障完整完成；同样容量和工作下不发生隔离丢弃。在线会话支持相同输入，整批容量校验仍在提交前执行。独立包/窗口/池核对、默认差分和迁移证据见[验收](../../fixtures/isolated_payload_release/README.md)。

## 有序put不保留完整请求缓存

当前持久载体按原生字节序号过滤重复包；只有连续接收前缀前进时，才向MRT1解码器交付新字节。因此网络重传不等于应用重复提交同一帧。仅使用v4注册源put时，可显式选择：

```json
"target_delivery": "carrier_once_put/1",
"target_cache_bytes_per_owner": 0
```

这两个字段放在`buffer_model`中。其余注册、源副本和本地copy容量必填；源隔离回收策略可同时使用。省略`target_delivery`时仍保留完整请求缓存，目标额度按累计接纳请求量检查。新模式必须为0，拒绝其他类型、策略和非零缓存额度。

使用[carrier-once-put.json](carrier-once-put.json)、上文8GPU小Clos和普通协议：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology /path/to/topology --protocol examples/scaleout_rdma/rdma.json \
  --config examples/scaleout_rdma_transactions/carrier-once-put.json \
  --backend /path/to/transaction-probe --output /dev/shm/new-carrier-once-put
```

六次put依赖前一次真实完成，复用同一目标地址；40B源复制池反复使用，完整目标请求缓存始终0。将命令改为`rdma-transaction-session`并传对应session程序，可按实际回调逐项追加。单次数据或ACK丢失仍由同一载体重传恢复，不改变请求/ACK或选路。

新模式仅保留每context的下一个合法MRT1序号，拒绝应用层重放、跳号和原子请求；不会把失败误写成完成。`transactions.target_delivery.contexts`报告实际`next_sequence/retained_requests/retained_encoded_bytes`；后两项应为0。解码临时内容、序号元数据、包和观测副本不在这个0中，不能称总目标内存为0。

该模式不撤回晚到包、不释放窗口或失败源lease，不提供跨连接恢复重放或GPU可见性保证。原有32远端操作等预算保持，不宣称无限连续服务。默认差分、真实重传、独立字节审计及迁移证据见[验收](../../fixtures/carrier_once_put/README.md)。
