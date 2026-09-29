# 最小联合执行程序

本目录的 `cosim-compute` 用锁定 ASTRA 的 Sys、Workload、HardwareResource 和 Chakra reader 执行一个合成 COMP。计算完成回调由 ns-3-UB 的事件队列触发。`cosim-write` 执行实际 ASTRA SEND、RECV 及其后续 COMP；只有 SEND 提交一份 UB 写事务。未配置传输描述的计算适配器仍拒绝收发。

从主仓库根目录操作。构建入口是 ns-3-UB，`backend/inject.cmake` 复用 M0 的核心库边界并加入计算目标；不能直接以本目录作为 `cmake -S`。完整的已执行配置、构建、测试命令及日志见 [M1.1 环境记录](../docs/environment.md#2026-09-28m1-1-compute-time)。

本机已构建的程序调用形式：

```text
build/m1-observation/output/cosim/ns3.44-cosim-compute INPUT.json SYSTEM.json NEW_RUN_DIR
```

功能输入见 [comp_1000ns.json](../fixtures/m1_compute/comp_1000ns.json)，ASTRA 配置见 [system.json](../fixtures/m1_compute/system.json)。运行目录必须不存在，其父目录必须已存在。程序保存输入副本、生成的 `compute.0.et`、ASTRA 执行日志和 `result.json`，不覆盖已有运行。

`start_ns` 是绝对起点，`duration_ns` 是非负整数持续时间。字段只接受 `moe-ub-compute/1`，并在写图前检查相加结果不超过 ns-3 的有符号 64 位纳秒范围。锁定 Chakra 分支把纳秒写入名为 `duration_micros` 的历史字段；本程序按该分支读取器的实际行为直接写入，不再乘除 1,000。零 runtime 且零 num_ops 的 COMP 由原 Workload 当作零时间节点移除，不占计算资源。

`UbNetworkAdapter::sim_schedule` 只接受锁定 Sys 提供的 NS 单位，正的小数纳秒向上取整，零也通过异步事件回调；负数、非有限值、错误单位、空回调和时间溢出均拒绝。`sim_get_time` 返回 ns-3 的绝对纳秒值，`sim_notify_finished` 报告完成而不停止其他事件。适配器及回调参数必须存活到相关事件执行完毕；本入口在事件队列排空后销毁 Sys 和适配器。每个进程只在事件运行前设置一次 ns-3 时间精度。

计算入口、时间适配器和 UB 使用 C++20；ASTRA 及独立的日志桥接使用 C++17。日志桥接隔离锁定日志头的 syslog 宏及 C++20 `format()` 歧义，不修改上游依赖。

检查入口为 `cosim-time-test`（时间边界）、它的 `--wrong-resolution` 独立进程，以及 `tests/integration/check_compute.py`（四项计算、非法输入、原 ASTRA issue/callback 日志、资源与库来源）。M0 原链接探针仍保留。配置会重写 UB 忽略的启动锁，操作前必须备份已知原文件，结束后恢复并检查原生关键产物。

这些结果只验证功能与时间接口。固定计算耗时和网络参数没有目标硬件校准依据。

## M1.3 收发分别完成

从主仓库根目录运行已构建入口（最后一个参数必须为新目录）：

```bash
build/m1-observation/output/cosim/ns3.44-cosim-write \
  fixtures/m1_transfer/single_write.json fixtures/m1_compute/system.json \
  third_party/ns-3-ub/scratch/2nodes_single-tp runs/my-write
```

程序复制网络配置、节点、拓扑、路由和 TP 配置，通过 UbUtils 创建两端设备及交换机，不读取或生成 `traffic.csv`。源 rank 0 的 Chakra 图含一个 SEND；rank 1 的图含一个 RECV 和依赖它的 COMP。两个图分别在 `start_ns` 和 `receive_start_ns` 激活，计算耗时为 `receive_compute_ns`。当前输入为 `moe-ub-write/2`，以显式接收起点取代旧版 `receive_first`；旧版输入明确拒绝。M1.2 历史二进制需要该任务历史输入，不能使用当前版本的 JSON。

`UbWriteTransfer` 保存一份描述和回调状态，检查 `(src_rank, dst_rank, tag, bytes)` 及 UINT8 类型。`transfer_id` 是内部 64 位标识，`task_id` 显式映射为 UB 的 32 位任务号；字节数也必须在 UB 的 32 位范围内，本步拒绝零字节。`tag` 限于 ASTRA NATIVE 不重映射的范围。业务优先级固定为原样例的 7，内部图和节点标识不参与选路。锁定 ASTRA 的 RECV 栈请求未初始化全部字段，因此本接口只保存显式实参所需信息，不读取或保留 `sim_request*`。

UB 补丁中的 `UbTransferExecutor` 保存任务值和完成接收者，复用 UbApp 原协议提交代码；它只复用 `RuntimeTask` 值类型，不查询 CSV 任务表或 phase。CSV 调用继续通知 UbTrafficGen，联合调用通知适配器。WQE 完成后安排同一仿真时刻的清理事件，退出 Jetty 内部回调栈后再释放 RTP Jetty、双向绑定和事务元数据；TP 保留或回收仍由原属性控制，CTP 共享 Jetty 保留。清理后再异步调用发送回调，`sender_completion` 固定为 `transaction_done`。适配器、描述及回调参数必须活到物理完成与回调执行结束。

目标端 `RemoteWriteCommitted` 在事务层更新 `m_memoryStore` 后、构造事务 ACK 前发出。RTP 在此之前按 PSN 去重、重组完整 TA 单元；不能使用去重前的最后包通知。`UbWriteTransfer` 按 taskId 和端点筛选，再由 `WriteCompletion` 按原 Jetty 与 TA 序号累计有效字节。目标端的 GetWqeSize 只是当前重组分段大小，整份任务大小取自不可变描述。

收齐数据的时刻记录为 `data_ready_ns`，通过 ScheduleNow 完成 RECV；源端 transaction_done 独立完成 SEND。数据先到时保留状态，晚到 RECV 在登记的当前时间异步完成，即使源端 Jetty 和 TP 已经释放。两侧回调返回且物理事务结束后，删除匹配上下文和分段表、清空回调参数、断开目标端通知，仅保留描述、字节计数和完成时刻。过期调用报告原 graph_id 和 transfer_id。当前入口每进程只有一份不可变任务，不进行跨图 taskId 复用或 M2 调度。

UB 的 `m_memoryStore` 记录模拟地址和写入大小，不是实际张量载荷或硬件内存一致性实现。这里的 data_ready 表示锁定模型的有效写入完成，不应据此推断尚未建模的主机行为。

UB 原交换机永久周期调度死锁检查，测试入口在 `start_ns + 1,000,000 ns` 停止；这只是功能测试的运行上限。实际事务必须在上限前完成，双端图完成、收发槽与执行上下文清空，否则检查失败。rank 完成通知不停止仿真，测试另安排后续事件验证这一点。联合程序使用 NS 精度，原 CSV 入口保留其 PS 精度；不直接将两种入口的完成数值混作同一个基准。

`cosim-write-test` 检查描述边界、接口匹配和独立完成状态机；其中人为指定的完成时刻只用于时序单元验证。`check_write.py` 运行真实 UB 的先接收、先发送、晚到接收、一字节及 TP 回收场景，并交叉检查有效写入通知、WQE 跟踪、ASTRA 原始 issue/callback 日志、两端图完成及上下文清理。`check_csv_regression.py` 使用保留的原版二进制核对原生结果，不会自行检出、升级或重编译依赖。

构建和实际检查结果见 [M1.3 环境记录](../docs/environment.md#2026-09-28m1-3-receive-ready)。本节说明收发完成语义，新增的最小端口观测见下节。持续服务调度、完整服务终止和模型校准属于后续任务。

## M1.4 端口计数与内部来源

在原写入口追加可选观测配置；省略它时保持 M1.3 的运行方式：

```bash
build/m1-observation/output/cosim/ns3.44-cosim-write \
  fixtures/m1_transfer/single_write.json fixtures/m1_compute/system.json \
  third_party/ns-3-ub/scratch/2nodes_single-tp runs/my-observed-write \
  fixtures/m1_transfer/observation.json
```

[observation.json](../fixtures/m1_transfer/observation.json) 选择交换机 3 的端口 0、egress 方向，在本地 `[0, 30000)` ns 内生成 1000 ns 窗口。窗口半开，最后一个可以不足整周期，无包窗口输出零。配置严格检查字段、正周期、有效区间和真实交换端口，本步最多 4096 个窗口。`record_provenance: false` 保留相同观测，关闭来源侧表和 truth 文件；它不改变业务输入。

计数复用 `UbPort::TraComEventNotify`：源码虽以完成事件命名，通知实际在通道 `TransmitStart` 成功后立即发出，参数 Time 是到完成的持续时间。本程序在通知当时计入 `Packet::GetSize()`，不把时间移到发送末尾，也不使用尝试发送前的旧 PortTxNotify。没有新增采样事件。累计总量另与原端口 GetTxBytes 的增量核对；不存在于包中的物理编码和 FEC 字节不计入。

源 Jetty 从当前 WQE 的真实进度保存 TA 分段的任务偏移。TP 构造 WRITE 数据包后，通过 `WritePacketCreated` 被动通知给出任务偏移、包内载荷偏移和载荷长度。后端以已有 Packet UID 建立侧表，处理端口发送事件时只读包副本，核对实际 RTP/TA 头和已有 FlowTag。未增加包头或包标签，不用操作身份改变优先级、选路或协议时间。来源开启时，未知 WRITE 来源或不匹配的范围明确报错。当前 UID 对应针对不会额外分片/合并的单任务 RTP 路径；不声称覆盖其他协议。

有效字节范围统一为半开区间。整包计数含业务载荷、数据包协议头和完整控制包；TP ACK、TA ACK 的载体字节以及信用帧不会因携带任务标签而获得业务归属。每次过端口都计数，来源侧表不会把重复经过的包从计数中去重。唯一交付量仍取 M1.3 的目标有效写入记录，不与多次经过或全网累计量混用。当前合成写无应用元数据，`payload_bytes` 与 `business_payload_bytes` 数值相等，两个字段仍分开保存。

| 文件 | 内容与用途 |
|---|---|
| observed/public_config.json | 本次最小输出版本、端口、计数口径、本地观测区间和采样周期 |
| observed/port_counters.jsonl | 每个本地窗口的整包字节及包数，含零窗口 |
| observed/endpoint_ops.jsonl | rank 内记录号、SEND/RECV 执行开始和各自完成时刻 |
| truth/operation_registry.jsonl | 合成操作、图节点与 rank 内端侧记录的对应 |
| truth/transfer_registry.jsonl | 操作、64 位传输号、UB 任务号、端点和有效数据范围 |
| truth/packet_ranges.jsonl | 所选端口实际经过的数据包及任务/包内字节范围，仅用于本步核验 |
| truth/port_byte_truth.jsonl | 每窗业务数据、应用元数据、数据包协议开销、控制包字节的分项 |
| truth/clock_models.json | 内部时钟模型；本步偏移和漂移均为零 |
| observation-audit.json | 内部测试计数及序列化包与发送时刻的 FNV-1a 指纹 |

端侧开始取自 ASTRA 调用实际进入适配器的时刻，完成取自传回 ASTRA 的对应回调；集成检查与原 ASTRA issue/callback 日志逐项核对。不能拿首包或末包时间代替它们。`observed/` 不含全局 operation_id、transfer_id、UB task_id、图节点号或时钟变换参数。现有 result.json、原始网络/ASTRA 日志、truth 和 audit 都是内部调试证据，不能作为关联方法可读输入。本轮文件是最小测试输出，M5 再落实规范中的完整目录、CSV、manifest、多设备时钟和研究导出。

`cosim-observation-test` 检查窗口边界、零窗口、控制包载体排除、包副本不改线速字节、精确范围及重复经过的计数。`tests/integration/check_observation.py` 检查 7 种场景、来源开/关/不挂观测共 21 次成功运行，以及 10 种非法配置；包含 1 字节、65,537 字节尾块、5,000 字节 TA 分段、晚到 RECV 加 TP 回收、反向纯控制端口和裁剪窗口。每组比较原生跟踪、ASTRA 执行、图、完成及资源状态，开关来源时观测文件与序列化包/时刻指纹相同。

标准 409,600 字节例的选定端口累计 417,834 字节：业务 409,600、数据包协议开销 7,800、控制包 434；data_ready 仍为 8,534 ns、SEND 完成仍为 8,622 ns。实际命令和保留的失败记录见 [M1.4 环境记录](../docs/environment.md#2026-09-28m1-4-port-provenance)。没有做真实丢包/重传集成、完整上游测试或硬件校准；当前校准状态仍为 unvalidated_reference。

## M2.1 固定四 rank 单层

生成器从 [layer.json](../fixtures/m2_single_layer/layer.json) 中的 Token、专家放置和固定计算 profile 构造 ExecutionPlan，校验后导出四份 Chakra 图。来源逐条保留请求、Token、专家及输入数组中的 source_record_index。生成计划只包含任务、字节和依赖，不包含预测的通信完成时刻。

以下命令已在本机对应入口验证。两个输出目录必须尚不存在，父目录必须存在；使用已有 Python 环境，不需要安装软件：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.generate \
  --input fixtures/m2_single_layer/layer.json --output-dir runs/my-layer-plan
build/m2-single-layer/output/cosim/ns3.44-cosim-layer \
  runs/my-layer-plan fixtures/m1_compute/system.json fixtures/m2_single_layer/network \
  fixtures/m2_single_layer/observation.json runs/my-layer-run
```

生成器在新输出目录用本机 protoc 从锁定 Chakra 的 et_def.proto 生成 Python 编码模块，然后以长度前缀 protobuf 写入 layer.0.et 至 layer.3.et。保留 plan.json、generation.json 的 schema、计划和图哈希以及 schema/compiler.json 的编译命令和结果。不会复用依赖目录中旧的未跟踪 et_def_pb2.py。当前系统 Python 没有 protobuf，已有 .venv/llmservingsim-native/ 中的 protobuf 可以执行导出。

cosim-layer 的参数依次是计划目录、ASTRA system.json、网络目录、观测配置和新运行目录；以 `-` 替代观测配置可不挂观测。后端在创建运行目录和网络前，核对所有计划字段、收发配对、唯一身份、局部依赖、发送顺序，并逐个读取四份实际 protobuf 文件，检查它们与计划一致。全部通过后创建同一网络、登记全部传输、装载四个 Sys，最后按 rank 顺序在同一 ready_ns 激活。

UbNetworkAdapter 的多传输入口按 `(src_rank, dst_rank, comm_tag)` 查找不可变描述，M1 的单传输入口仍可使用。M2.1 每条任务继续复用 UbWriteTransfer 的真实数据就绪、事务完成、异步回调与清理路径。锁定 ASTRA 的 HardwareResource 已有一个 SEND 槽，RECV 不占该槽；本轮没有修改 ASTRA、LLMServingSim 或 UB 源码及版本。

图中 SEND 按操作内目的 rank 排序。专家计算等待本 rank 的 Dispatch 发送完成、接收就绪和本地输入；本地 Combine 的发送、接收及解包合并全部完成后才执行后继 COMP。固定例有 28 个通信节点和 20 个计算节点，共 48 个节点。局部耗时、网络配置及输入边界见 [样例说明](../fixtures/m2_single_layer/README.md)。当前保留 tag 小于 500000000 的已验证范围，超界明确拒绝，以避开锁定 ASTRA NATIVE 路径的标签重映射。

端侧输出为四个 rank 各自的 Dispatch/Combine 两条记录，共八条。为保留当前步骤的最小实现，结束时先刷新 ASTRA 日志，再读取其原始 issue/callback 记录：Dispatch 从准备完成到专家计算开始，当前固定图中后者已核对等于本地 Dispatch 条件最晚完成；Combine 从本地专家结果处理完成到最终解包合并完成。记录缺失、重复或依赖矛盾会报错，不从网络包推测时刻。当前入口要求 trace-enabled=1 和正计算耗时，不把未记录的零时间节点当成已验证阶段标记。

来源侧表扩展为按 UB task_id 区分十四条传输；端口仍复用 M1 的成功发送计数位置。observed 中只含局部操作号、端侧本地时刻和端口窗口，两个全局操作号、跨 rank 对应、传输号和有效范围保存在 truth。完整 plan.json、result.json、ASTRA/网络日志和 observation-audit.json 都是内部调试证据。

`tests/unit/layer_plan_test.py` 核对矩阵、八份专家输入、局部依赖、身份与边界；`tests/integration/check_layer.py` 运行来源开/关/不挂观测，检查原 ASTRA 的 96 条 issue/callback、十四份真实 UB 写、每 rank 最多一个 SEND 及发送在途时 RECV 仍能登记，并拒绝损坏图和不一致计划。M2.1 的指定场景中 rank 0～3 分别在 21168、21506、21552、17675 ns 完成；这组数值是实际运行结果，不写入生成器或后端作为预设通信时间。

以上是 M2.1 的原始验收范围，后续 M2.2～M3.2 检查见下文；模型校准尚未完成。完整命令和实际结果见 [M2.1 环境记录](../docs/environment.md#2026-09-28m2-1-single-layer)。

## M2.2 等待与延迟反馈

新增专用测试目标 `cosim-layer-delay-test`，复用原 ASTRA 执行层、UbNetworkAdapter 的 ns-3 时间接口和 WriteCompletion；SEND/RECV 用给定事件替身驱动，测试中不创建 UB 网络对象。`cosim-layer` 继续只使用真实 UB，没有替身开关或预设通信时间。

完整检查命令如下，输出目录须为新目录；构建配置记录在本步环境记录中：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_delay.py \
  --build-dir build/m2-delay-feedback --output-dir runs/my-delay-check
```

脚本生成一组四 rank 图，执行五组给定事件及两组真实 UB 对照。每次根据传输事件独立计算全部必要本地条件的最晚时刻，再与原 ASTRA 的 96 条 issue/callback 及结果核对；检查固定计算耗时、Combine 前驱、发送槽、回调和最终资源释放。所有运行的二进制图哈希相同。给定事件专用入口的参数为 `PLAN_DIR SYSTEM.json EVENTS.json NEW_RUN_DIR`，完整调用保存在检查输出的 commands.json 中。

规范的专家计算 26000/38000 ns，以及最后必要输入推迟后的 30000/42000 ns 已通过；本地发送较晚时必须等待，非关键和其他 rank 输入较晚时不会额外阻塞 rank 2。真实链路传播延迟对照中，rank 2 专家开始从 5877 移至 11629 ns，均早于全组 Dispatch 最晚完成。变化由实际 UB 事件产生，不能当作链路延迟与整图耗时的一般比例。

[事件输入说明](../fixtures/m2_delay_feedback/README.md) 列出每项对照。输出含原始日志、给定事件、网络副本、图、每个子进程状态以及 summary.json 中的逐 rank 等待依据；这些都是内部测试证据。十项非法输入/覆盖拒绝、M2.1 的来源开关与错误图回归、52 个 ASTRA 对象及单套 10 个 UB ns-3 库检查通过。实际命令和两次已解决的检查失败见 [M2.2 环境记录](../docs/environment.md#2026-09-28m2-2-delay-feedback)。M2.3～M3.2 的后续验收见下文；模型仍未校准。

## M2.3 去重、分块与发送槽

生成器现按 chunk_bytes 将有向向量分成独立 UB 写任务，末块保留真实字节数。前后端都检查块编号、完整向量范围、尾块、来源和配对；task_byte_range 始终是块内范围，pair_byte_range/pair_payload_bytes 标识原向量位置与总长。内部 transfer_registry 输出相应 chunk_id 和向量范围，observed 不增加隐藏身份。生成器复制输入后再构图，后续修改配置不会改变已经生成的计划。

联合入口从计划读取 send_slots_per_rank，在四 rank 激活前调用 ASTRA 的 configure_p2p_send_slots。该显式配置仅作用于点对点 SEND；按节点身份占用和释放，乱序完成可释放对应槽。RECV 不占槽，原集合通信继续独占原通信资源。未启用时走原始单槽路径，M1 和原 native 入口保持默认行为。HardwareResource.hh/.cc 补丁及其单元测试在 ASTRA 提交 3f72d0a，对应的 LLMServingSim gitlink 提交为 47b3a21。这两个历史提交已包含在各自维护仓库的 cosim 分支中，于 2026-09-28～29 完成发布和远端获取验证；当前采用版本见根目录 deps.lock.json。

以下完整检查已执行，输出目录必须尚不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_variants.py \
  --build-dir build/m2-chunks-slots --output-dir runs/my-variant-check
build/m2-chunks-slots/output/cosim/ns3.44-cosim-send-slots-test
```

单独运行某份输入时，继续使用生成器和 cosim-layer：输入可选 fixtures/m2_variants/dedup.json、chunks.json、two_slots.json，真实执行程序使用 build/m2-chunks-slots/output/cosim/ns3.44-cosim-layer。参数顺序与 M2.1 相同。本步支持一槽或两槽、最多 4096 条传输；计算和元数据的已有约束不变。单块不得超过 2^32−1 字节，大向量分块的边界仅有生成/校验证据。

去重后实际为十二次写、98304 字节、2/1/4/1 份专家输入。3072 字节分块后为四十二次写、114688 字节、2/2/3/1 份专家输入；改成两槽后图和字节不变，四 rank 的 SEND/WQE 峰值从 1 增至 2。三种输入的来源开/关/不挂观测结果一致，原 ASTRA 事件、收发回调、资源释放和端口分项均通过。样例定义见 [输入说明](../fixtures/m2_variants/README.md)。

M2.1/M2.2、M1 写/观测/时间/计算及单套 ns-3 链接回归通过。`check_native_collective.py` 在独立 analytical 构建中比较补丁前后默认资源行为：八 rank、十六个集合通信节点的 64 条执行事件与完成时刻相同；该原生后端未链接进联合程序。实际命令、保留的检查失败及停止位置见 [M2.3 环境记录](../docs/environment.md#2026-09-28m2-3-chunks-slots)。


## M2.4 连续两张固定图

`moe_ub_cosim.sequence` 将两份明确给定的解码输入导出为 0/、1/ 下的计划与四 rank Chakra 图，以及 sequence.json。RunIdentities 在本次运行内统一分配 transfer_id、UB task_id 和 comm_tag；图/操作身份也不得重复。局部 node_id 可复用，第二轮 Token 索引加一。输入中的 ready_ns 均为零，表示尚未提交的固定步骤；实际第二轮提交时间由第一轮完成事件决定，运行目录 1/plan.json 保存该绝对时刻。

已验证的调用如下，两个输出目录须尚不存在。构建配置与完整回归命令见 [M2.4 环境记录](../docs/environment.md#2026-09-28m2-4-graph-lifecycle)。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.sequence \
  --input fixtures/m2_sequence/two_steps.json --output-dir runs/my-sequence-plan
build/m2-graph-lifecycle/output/cosim/ns3.44-cosim-sequence \
  runs/my-sequence-plan fixtures/m1_compute/system.json fixtures/m2_single_layer/network \
  fixtures/m2_single_layer/observation.json runs/my-sequence-run
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_sequence.py \
  --build-dir build/m2-graph-lifecycle --output-dir runs/my-sequence-check
```

cosim-sequence 复用 cosim-layer 的真实执行代码，入口只接受两张固定图。启动前校验两组图和运行内身份；提交时先核对四 rank 已完成，再检查并准备全部 feeder，装载全部图，登记该轮传输，最后按 rank 顺序激活。首轮同样先装载全部 rank，再登记传输并激活。四个 Sys、应用、UB 控制器、网络与时间线始终保留。提前声明传输来源不提交 UB 任务，SEND 仍须由 ASTRA 依赖派发后提交一次实际写。

ASTRA 的新接口仅在 bind_graph 后启用。load_graph 不发任务，activate_graph 才执行；忙时装载、身份复用、未激活就 fire 和重复激活均报错。回调保存 graph_id、node_id 和通信匹配 tag，UB 描述侧表对应唯一传输；在查找节点和释放资源前识别过期回调，日志保存旧图、当前图、rank、node 和 tag。默认原生行为保留，本轮 ASTRA 补丁为 e2a14f5，对应的 LLMServingSim gitlink 提交为 0038df8。相关提交已发布到各自维护仓库的 cosim 分支；LLMServingSim 随后的 9aed949 仅更新 ASTRA 维护地址，ASTRA 仍固定在 e2a14f5。发布位置与独立获取结果见[开发管理说明](../docs/development.md#4-上游修改怎样保存)和[环境记录](../docs/environment.md)。

正常两轮共 28 次 UB 写、229376 业务字节，96 个节点产生 192 条实际 issue/callback。第二轮在 21552 ns 提交，全组在 43145 ns 完成。观测窗口贯穿两轮，端侧局部记录号为每 rank 的 1～4；图/操作/传输对应保留在内部文件，observed 不新增全局身份。来源开/关/不挂观测及重复执行一致。

cosim-lifecycle-test 是独立测试目标：使用同一真实网络执行，额外注入五个已退休的第一轮回调并尝试非法提交；不向 cosim-sequence 提供故障或替身开关。非法动作被拒绝，正常业务事件、33 份网络跟踪及观测与对照一致。测试结束保留借用回调释放、资源归零和原生路径结果。

本步仍限固定四 rank、正计算、零应用元数据和两轮；不实现动态请求或 M3 调度协议。comm_tag 继续采用原已验证的 1～499999999 范围以避开 NATIVE 重映射，编号耗尽时失败且分配状态不变；没有宣称完整正 31 位空间均可用。实际场景与未验证范围见 [输入说明](../fixtures/m2_sequence/README.md)。

## M3.1 单 EP 组服务入口

`python -m moe_ub_cosim.service` 直接使用锁定 LLMServingSim 的 Scheduler、Request、Batch 和 KV 分配接口，调用 M2 图生成器；`cosim-service` 复用原 ASTRA 执行层、图生命周期接口和 UB 适配器。这是联合运行的新入口，上游 `python -m serving` 仍使用原生流程。本步未修改子模块源码或提交指针。

在已按本步环境记录配置的构建目录中运行，输出目录须为新目录：

```bash
cmake --build build/m3-service-loop --target cosim-service --parallel 8
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m3_service/service.json --layer fixtures/m2_single_layer/layer.json \
  --backend build/m3-service-loop/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m2_single_layer/network \
  --observation fixtures/m2_single_layer/observation.json --output-dir runs/my-service
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service.py \
  --build-dir build/m3-service-loop --output-dir runs/my-service-check
```

本机完整配置命令及日志见 [M3.1 环境记录](../docs/environment.md#2026-09-28m3-1-service-loop)。`--observation` 可省略；前端默认壁钟等待上限为 60 秒、仿真失败上限为 1000000 ns，超限报告失败，不作为正常服务完成时间。

后端先创建全部执行和网络对象，用仅含元数据的四份 Chakra 图完成 0 ns 初始化；没有业务节点、包或端侧操作。后端 stdout 只输出 cosim/1 DECIDE 消息，前端返回匹配的 COMMIT。收到 WAVE_DONE 后，前端把 current_ns 设置为后端绝对时间，通过 add_done 更新请求，然后调用 schedule 选择下一轮批次；图按这次实际选中的请求及其 Token 下标生成。等待 Python 的现实时间不计入仿真。

后端每轮先检查四份图和侧表、身份及 rank 空闲条件，全部装载并登记后才按 rank 顺序激活。rank 完成消息汇聚为全组 WAVE_DONE；接收登记不产生额外写任务。四个 Sys、应用、控制器、连接和 ns-3 时间线跨轮保留。前端结束后还核对 UB 事务、实际链路包到达和端口队列；有包或端口发送未收尾则继续处理实际事件，收尾完成后停止并销毁剩余巡检定时事件。不会等待原生分配器的周期死锁巡检自行消失。

输出中的 input.json/layer.json 是输入副本；graphs 保存各次实际决策生成的图与计划，bootstrap 保存零业务初始化图。protocol.jsonl 保存双方消息；service_result.json 保存逐请求初值、调度前后和完成后状态；execution/result.json、原 ASTRA 日志和 UB 跟踪用于核验实际事件。manifest.json 保存命令、调度器来源、给定 KV/路由/计算范围和正常或失败状态。以上均是内部功能验证资料；可选的 execution/observed 与 execution/truth 继续遵守观测和内部真值的访问边界，不声称已经实现 M5 的正式研究输出。

验收覆盖三轮共 42 次 UB 写、344064 业务字节、144 个节点的 288 条原始执行事件，四个请求分别从不同 Token 下标推进三次并结束。基线完成时间为 64738 ns；仅增加 rank 1 链路传播时延后为 100410 ns，下一次服务决策跟随实际 UB 完成。来源开/关/不挂观测、重复执行以及每次回复前暂停 30 ms 的运行保留相同业务时刻；改变端口间隔的收尾测试确认服务结束后仍会等网络空闲。EOF、非法消息、编号/时间不符、输入耗尽却含未来唤醒、缺少工作、错误路径和末 rank 坏图被拒绝；子进程失败及不完整行的等待超时可报告。

以上为 M3.1 单组基线，该样例的 next_wakeup_ns 为 null。M3.2 已增加下述动态到达和第二组；四 rank 正计算、每 rank 每轮一个 Token 的限制继续保留，M3.4 的零 Token rank 尚未实现。它仍是未校准的单层功能模型，不是完整模型服务性能结果。具体初始长度见 [服务样例](../fixtures/m3_service/README.md)。

## M3.2 动态到达与独立组

服务前端按绝对 `arrival_ns` 接纳请求，后端在同一 ns-3 事件队列登记 `next_wakeup_ns`。每次 COMMIT 替换此前的外部唤醒；图提前完成时重登记剩余到达时刻，全空闲时直接推进到下一次到达。后端按显式 EP 组维护在途图和完成集合，单个成员提前结束不会释放整个组，也不会阻挡另一个空闲组提交。

新请求到达忙碌组只更新前端 `pending` 队列，不调用该组的 schedule 或替换图。组上的旧请求全部结束后，才经原 Scheduler.add_decode 接纳下一批给定 KV 请求；每个请求的下一步路由和图仍根据真实批次生成。输入限定一或两个四成员组、每批四成员同时到达且剩余步数一致、每 rank 每轮一个 Token。具体容量与输入边界见 [动态输入说明](../fixtures/m3_dynamic_arrivals/README.md)。

使用 [M3.2 环境记录](../docs/environment.md#2026-09-29m3-2-dynamic-arrivals) 中配置的独立构建目录，输出目录必须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m3_dynamic_arrivals/two_groups.json --layer fixtures/m2_single_layer/layer.json \
  --backend build/m3-dynamic-arrivals/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m3_dynamic_arrivals/network \
  --observation fixtures/m3_dynamic_arrivals/observation.json --output-dir runs/my-dynamic-service
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_dynamic_service.py \
  --build-dir build/m3-dynamic-arrivals --output-dir runs/my-dynamic-check
```

`deployment.json` 保存组、rank、UB node 与专家放置，C++ 在创建执行对象前读取。前端仍接受不带 groups 的 M3.1 输入；直接调用后端且不提供 deployment.json 时，仅采用原四 rank ep0 定义。可用 rank 按整个组报告，后端仍先校验一组全部图和侧表、再全部装载和登记、最后激活；旧组的 UB 对象和未完成事务继续存在。`service_result.json` 增加实际到达、各次决策后的排队请求和在途图记录；`pending` 与上游调度器内部 waiting 分开记录。

实测在 5200 ns 给忙碌组排队，5500 ns 启动第二组，此时第一组仍有真实 UB 写在途。双组共九轮，126 次写、1032192 业务字节，最终在 129517 ns 完成并排空网络。来源开关和不挂观测保留相同业务结果。M3.1、固定单层、连续图、去重/分块/发送槽及延迟反馈回归通过；本轮没有修改依赖。

两个组的功能输入使用同一仿真内的两个交换域，没有共享端口；共享瓶颈效应留在 M3.3。模型仍为未校准的合成单层，M3.4 完整边界覆盖、P/D 和真实数据未在本轮实现。
