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

输出中的 input.json/layer.json 是输入副本；graphs 保存各次实际决策生成的图与计划，bootstrap 保存零业务初始化图。protocol.jsonl 保存双方消息；service_result.json 保存逐请求初值、调度前后和完成后状态；execution/result.json、原 ASTRA 日志和 UB 跟踪用于核验实际事件。manifest.json 保存命令、调度器来源、给定 KV/路由/计算范围和正常或失败状态。以上均是原服务入口的内部功能验证资料；可选的 execution/observed 与 execution/truth 保留早期调试格式。正式研究输出由下文 M5.1 入口生成。

验收覆盖三轮共 42 次 UB 写、344064 业务字节、144 个节点的 288 条原始执行事件，四个请求分别从不同 Token 下标推进三次并结束。基线完成时间为 64738 ns；仅增加 rank 1 链路传播时延后为 100410 ns，下一次服务决策跟随实际 UB 完成。来源开/关/不挂观测、重复执行以及每次回复前暂停 30 ms 的运行保留相同业务时刻；改变端口间隔的收尾测试确认服务结束后仍会等网络空闲。EOF、非法消息、编号/时间不符、输入耗尽却含未来唤醒、缺少工作、错误路径和末 rank 坏图被拒绝；子进程失败及不完整行的等待超时可报告。

以上为 M3.1 单组基线，该样例的 next_wakeup_ns 为 null。M3.2 已增加下述动态到达和第二组；M3.4 已按下文允许零 Token 成员。它仍是未校准的单层功能模型，不是完整模型服务性能结果。具体初始长度见 [服务样例](../fixtures/m3_service/README.md)。

## M3.2 动态到达与独立组

服务前端按绝对 `arrival_ns` 接纳请求，后端在同一 ns-3 事件队列登记 `next_wakeup_ns`。每次 COMMIT 替换此前的外部唤醒；图提前完成时重登记剩余到达时刻，全空闲时直接推进到下一次到达。后端按显式 EP 组维护在途图和完成集合，单个成员提前结束不会释放整个组，也不会阻挡另一个空闲组提交。

新请求到达忙碌组只更新前端 `pending` 队列，不调用该组的 schedule 或替换图。M3.2 基线在组上的旧请求全部结束后，经原 Scheduler.add_decode 接纳下一批给定 KV 请求；每个请求的下一步路由和图仍根据真实批次生成。M3.2 的输入限定一或两个四成员组、每批四成员同时到达且剩余步数一致、每 rank 每轮一个 Token；M3.4 放开后的范围见下文。具体容量与输入边界见 [动态输入说明](../fixtures/m3_dynamic_arrivals/README.md)。

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

M3.2 的两个组使用同一仿真内的两个交换域，没有共享端口；共享瓶颈另用下述 M3.3 输入验证。模型仍为未校准的合成单层，M3.4 完整边界覆盖、P/D 和真实数据未在本轮实现。

## M3.3 共享瓶颈对照

沿用 M3.2 服务入口和执行协议，新增八 NPU、两个交换机的输入。每组一半成员接交换机 8，另一半接交换机 9；400 Gbps 接入汇入共同的 10 Gbps 交换机间链路。两个四请求组分别在 0、5500 ns 到达，各执行三轮。服务调度和通信完成逻辑未改动，子模块源码和版本不变。

按 [M3.3 环境记录](../docs/environment.md#2026-09-29m3-3-shared-bottleneck) 配置构建后，下面命令已实际执行；输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_shared_bottleneck.py \
  --build-dir build/m3-shared-bottleneck --output-dir runs/my-shared-check
```

检查分别运行两组、同一网络重叠运行、仅延后第二组到 500000 ns、来源关闭、不挂观测和重复执行。逐轮比较相同 Token 路由、字节、计算和依赖，逐条核对实际 ASTRA/UB 事件，并检查组完成与下一次提交的对应。重叠首轮 ep0/ep1 在 85934/85997 ns 完成，比独立运行晚 30896/29280 ns；专家计算和下一轮提交也随实际完成移动。第二组启动时，第一组的一个 4174 字节包仍在共享出口等待。错开后两组事务和该出口的业务窗口不重叠。

`PortObservation` 在已有成功发送事件之外读取原生物理出口 FIFO 入队/出队事件，输出执行目录中的 `egress-queue-audit.jsonl`。每条含事件、仿真纳秒、包 UID、整包字节、事件后的排队字节；发送开始另含原生发送时长决定的 `busy_until_ns`。它是内部审计，不进入 `observed/`，不添加包标签、改写报文或登记仿真事件。来源关闭仍保留该审计，不挂观测则不生成；各模式的业务时间、协议与原生网络跟踪一致。

该 FIFO 不包含 VOQ、信用等待或当前正在发送的包。其峰值不必随竞争增加：本次重叠峰值 4174 字节，独立运行峰值分别为 4174/4236 字节；两组平均 FIFO 等待及累计排队量增加，连续忙碌时间增长。内部包来源按窗口分组，逐项核对端口聚合字节，不把隐藏身份加入观测。具体矩阵与结果见 [样例说明](../fixtures/m3_shared_bottleneck/README.md)。结论限于此未校准功能输入，未验证 M3.4 的完整边界与故障。


## M3.4 服务边界与故障记录

服务输入现在允许 0～64 个请求，每个请求有 1～32 步显式路由，分别指定到达时刻。保留一或两个四成员 EP 组、给定已就绪 KV、每 rank 每轮最多一个真实 Token。组空闲时，仅给本地 Scheduler 已空的 rank 按 FIFO 接纳一个等待请求；其他成员可以继续旧请求。组执行期间，新到达请求只排队，成员提前完成仍须等整组完成，才可调度下一轮。

每轮仍校验并装载全部四份图。没有本地 Token 的成员不生成 attention/打包、Combine 解包合并或下一层计算；有远端专家输入时仍执行接收、专家计算、局部合并及发送结果。没有专家输入则省略相应计算，保留必要收发依赖。完全空闲成员的图只有元数据，阶段节点为 null，在激活时完成；不调用虚构请求的 schedule/add_done。纯本地专家计算不产生网络写，空请求服务在 0 ns 正常结束。

使用 [M3.4 环境记录](../docs/environment.md#2026-09-29m3-4-service-boundaries) 中的独立构建配置，运行以下已验证入口，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service_boundaries.py \
  --build-dir build/m3-service-boundaries --output-dir runs/my-boundaries-check
```

[边界输入](../fixtures/m3_service_boundaries/README.md) 覆盖稀疏参与、不同到达与步数、另一组先结束、纯本地和空输入，逐 Token 检查真实图和 UB 写。稀疏三轮共六次写、49152 业务字节，于 45502 ns 完成；不均匀请求共五轮，于 59263 ns 完成。观测开启、关闭、不挂载及重复运行保留相同协议、业务时刻与原生网络日志。input_exhausted 不代替 service_finished，服务完成仍等实际链路包与队列排空。

运行中可捕获的异常输出 execution/failure.json，含阶段、决策号、时间、最后提交、组/图/rank、待完成传输和网络资源。前端 failure.json 保存尚未完成的请求、队列和图，manifest.json 保留后端实际退出码；EOF 时先回收已终止子进程，避免清理动作覆盖其退出原因。后端被信号直接终止时只能保存前端状态和现有日志，不声称 C++ 已正常清理。

死锁检查在真实事件后于同一仿真时刻触发：没有未来唤醒、计算/适配器事件、待提交或回调事件、UB 待执行任务及网络数据，而图仍等待时，立即报告位置。仍有 UB 任务或网络状态但无法前进时，仿真时间上限报告失败和资源快照，不将其称为已证明死锁；没有固定轮询或固定通信时间。独立 cosim-service-deadlock-test 只用于遗漏一个成员激活的故障检查，正式程序没有这种配置开关。

ASTRA 日志使用固定依赖的单线程异步写入。根仓库日志桥接层在导出前等待队列中的 flush 完成，专项测试阻塞写线程核对等待与完整记录；这种主机同步不推进仿真时钟。M1/M2 及 M3.1～M3.3 回归通过，子模块源码和版本不变。以上仍为未校准的单层合成验证，不包含 P/D、真实路由、容量争用或丢包恢复。

## M4.1 单请求预填充与 KV 交接

同一个服务入口新增 `moe-ub-pd-fixture/1` 输入，执行一个合成请求的预填充、一次 KV 交接和后续解码。P/D 各有四个 EP 成员、两组不重叠，交接时两组均可提交。模型明确为一层 `cosim_tiny_moe`、MHA、TP=PP=1；模型、独立 KV dtype、显式源/目的 rank、缓存布局和每 rank 内存预算见 [P/D 样例](../fixtures/m4_pd_handoff/README.md)。

LLMServingSim 的原生 `pd_type="prefill"` 会把流式 KV 纳入原批次并自动释放源缓存，与本步独立交接图的边界不同。根前端使用其 `colocated` Scheduler 完成正常批次，保留真实 Request、MemoryModel 和 KV 池，在 P 完成后明确暂停 P 调度。目标容量先分配；后端原子校验八份 KV 图，再全部装载、登记和激活。只有所有成员、接收和源事务均完成，才经 `cosim/1` 的 `KV_DONE` 同时释放两组。前端据此释放 P 缓存、调用 D 的 `add_decode` 复用预留，并开始下一步。没有修改依赖或调用原生解析 KV 流量生成器。

按 [M4.1 环境记录](../docs/environment.md#2026-09-29m4-1-pd-handoff) 配置并构建 `build/m4-pd-handoff` 后，可从根目录运行以下已验证入口，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m4_pd_handoff/service.json --layer fixtures/m4_pd_handoff/layer.json \
  --backend build/m4-pd-handoff/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m4_pd_handoff/network \
  --observation fixtures/m4_pd_handoff/observation.json --output-dir runs/my-pd-handoff

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_pd_handoff.py \
  --build-dir build/m4-pd-handoff --output-dir runs/my-pd-check
```

`model.json` 保存本次模型副本，`service_result.json` 增加预填充/KV/解码轮次及权重、工作区、缓冲区、KV 块和队列的生命周期快照；失败时相同快照写入前端 `failure.json`。KV 的 ExecutionPlan 和 Chakra 图沿用 `graphs/gN/`，逐分片明确 K/V、Token/头范围、字节范围与运行内身份。新增 `KV_TRANSFER` 端侧记录仍不带内部操作、请求或传输身份；来源信息只在 truth 下保存。

默认四 Token 的有效 BF16 KV 为 4096 字节，分六次真实 UB 写，源/目标各占一个 16384 字节容量块。P 在 40159 ns 完成，KV 最后数据于 40830 ns 就绪，最后事务于 40876 ns 完成，D 同时开始；请求在 69994 ns 结束。增大目标链路时延后，KV 事务及 D 开始均后移 24000 ns。七次正常运行、十一项预期失败、29 项 Python 单元及受影响的 M1～M3 回归通过。

当前计算仍复用单层五阶段合成功能 profile；`next_layer` 字段是沿用的阶段名称，不代表本例增加了第二层，也不表示逐算子或数值前向已验证。模型、路由和时序均未校准。本步只完成 T10 的单次交接部分；多请求交接排队和 KV 与独立组竞争留在 M4.2，未提前实现。

## M4.2 交接队列与第三组竞争

服务入口增加 `moe-ub-pd-queue-fixture/1`，管理最多八个 P/D 请求及八个独立组的给定 KV 请求。三个组分别使用 rank 0～3、4～7、8～11；后端按显式成员集合识别一个计算组或两个交接组，确认所有成员空闲后才装载图，第三组保留原执行上下文与网络对象。旧 M3 和 M4.1 输入继续使用原前端。

待交接请求在 P 完成时按 FIFO 入队，同一轮完成按请求编号排序。队首在两组可提交且 D 容量充足时优先处理；容量不足时源 KV 和 P 组保留，D 继续解码释放容量。目标按请求剩余生存期所需块预留，避免后续增长引起抢占，只有实际已计算 KV 进入网络。KV_DONE 后释放源并让 D 复用目标块；所有源交接完成后 P 才恢复新一轮。请求、块池、批次仍由锁定的 LLMServingSim 对象维护。

按 [M4.2 环境记录](../docs/environment.md#2026-09-29m4-2-pd-queue) 构建 `build/m4-pd-queue`，从根目录运行以下入口，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m4_pd_queue/service.json --layer fixtures/m4_pd_queue/layer.json \
  --backend build/m4-pd-queue/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m4_pd_queue/network \
  --observation fixtures/m4_pd_queue/observation.json --output-dir runs/my-pd-queue
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_pd_queue.py \
  --build-dir build/m4-pd-queue --output-dir runs/my-pd-queue-check
```

[样例](../fixtures/m4_pd_queue/README.md) 比较 P/D 单独、独立组单独、共享链路重叠、错开、增加容量和忙碌期间到达。真实重叠使首个 KV 完成及 D 开始均后移 26143 ns；同一 EP 业务包跨 KV 图启动保留在队列中。九次正常运行、八项拒绝、33 项 Python 单元及 M1～M4.1 回归通过。记录仍是未校准合成单层，组内异步计算、布局转换及真实输入不在本步内。


## M5.1 多端口采集与本地时钟输出

`python -m moe_ub_cosim.research run` 读取 `moe-ub-research-run/1` 配置，调用已有服务入口及 `cosim-service`，在执行结束后生成规范第 10.6 节的研究目录。它接受现有普通 EP、P/D 和数据服务输入；入口仍限 service，没有增加模型层数或另一套执行引擎。配置、字段、三个实际命令和完整限制见 [研究输出样例](../fixtures/m5_observation/README.md)，构建及本机日志见 [M5.1 环境记录](../docs/environment.md#2026-09-29m5-1-observation-export)。

后端观测参数新增 `moe-ub-port-observation-set/1`，只含 schema_version 与 ports 列表；列表每项使用原单端口配置，最多 16 个不同的 switch/port。每个挂接点被动保留实际发送开始时间、包 UID、整包与业务字节到 `execution/ports/<switch>-<port>/packet-events.jsonl`，并沿用原端口计数和来源侧表。输入中的单端口 v1 和 `-` 继续保留原路径与语义。执行结果另记录实际 RNG seed/run，不改变随机设置。

Python 为所有 rank 与被观测交换机校验唯一时钟，使用整数 ppb 和向下取整转换实际 ASTRA 阶段/端口事件。设备时钟和本地窗口不进入后端调度参数，不发包、不改变包内容、路径、优先级或仿真事件。按本地半开区间逐窗输出，包含零值和不足整周期的尾窗；端侧 local_record_id 在各 rank 内重新连续编号，不编码全局步号。

根 `observed/` 只含端侧五字段、端口窗口 CSV 与白名单静态配置。操作/传输身份、逐 Token 或 KV 范围、图、全局时刻和时钟参数保留在 truth、graphs 和其他内部资料。`validate-observed` 仅访问三个观测文件；`validate-run` 读取内部原始事件、重建输出并检查文件摘要、唯一交付、整包分类与操作归属。原服务输出存于 logs/raw-run，包括其中的旧格式 observed 子目录；这些原始内部资料不能交给关联方法。

最终双组样例生成 48 条端侧记录、276 个窗口（151 个零窗口），业务仍为 84 次写、688128 字节、264523 ns。来源开/关的观测完全一致；改变时钟或采样区间、重复执行及不挂观测，业务图、协议、ASTRA/UB 时刻和原生网络日志保持相同。九次正常研究运行、一次无观测对照、十五项预期失败、45 项 Python 单元以及受影响的 M1～M4 回归通过。校准状态仍为 unvalidated_reference，完整多层与目标通信校准留待后续任务确认。


## M5.2 多层连续图

`moe-ub-multilayer-service/1` 在已有 DataService 中逐层读取路由和参考计算，调用单层生成器后合并为一份 `moe-ub-multilayer-plan/1`。每 rank 一张 Chakra 图包含选定层，各层根节点只依赖本 rank 前层末节点；空层传递此前依赖，真实下一层替代旧 next_layer 参考计算。全部层执行完后才完成一个服务 Token，同一后端与网络跨层、跨步保留。

单层后端校验拆为内存语义检查与 Chakra 文件检查，原入口行为保留。新增 multilayer_plan.cc 逐层调用原语义检查，独立重建节点/传输映射及本地边界，对全部文件校验后才装载和激活。来源侧表保存逐层计划，只有合并图进入 ASTRA。局部节点号在层间移位，操作/传输/UB 任务/匹配 tag 在同一运行内不重复；层序、Token 连续性、计算和共享配置不符时拒绝。

端侧输出按每层的实际 phase_nodes 与 entry_deps 取 ASTRA 时间，内部记录号为每层预留两个位置，再跨图累加；M5.1 正式导出继续独立按 rank 重排本地编号。没有新增观测字段，图、层和源索引只存在于内部资料。研究清单另明确 executed_layer_ids、latency_scope 和 full_model_execution_validated，避免把完整收尾误写成完整模型性能验证。

按 [M5.2 环境记录](../docs/environment.md#2026-09-29m5-2-multilayer-continuous) 构建 build/m5-multilayer 后，使用 [连续样例](../fixtures/m5_multilayer/README.md) 的已验证 run、validate-run、validate-observed 和集成测试命令。三个层、三个解码步为 78 次写、638976 字节、193789 ns；实际存在本 rank 下一层已开始而远端前层尚未结束的时段。八次研究运行、无观测对照、十项拒绝和相关旧入口回归通过。

多层范围仍限一个四成员组、给定就绪 KV 解码、明确的连续 MoE 层和参考阶段耗时。32 层两步验证 558 次写、4571136 字节，但没有 embedding/输出头完整算子图、真实路由或目标 NPU profile。P/D 保持 M4 的单层功能范围；完整模型时延与目标通信行为仍未校准。


## M6 单层执行参数化

`cosim-layer` 和固定两轮的 `cosim-sequence` 按输入成员建立 ASTRA 图及 UB 应用，不再限定四个 rank、八专家、Top-2 或恒等端点映射。专家数量由放置表长度确定；Python 与 C++ 校验成员、Top-K、非均匀放置、端点映射、来源和计算/字节溢出。计算端点是否存在、是否为交换节点，在网络节点创建后、执行图激活前检查；失败不会发起业务传输。

逻辑 rank 保留在 Chakra 匹配键、图和端侧操作中，实际 UB 提交和目标完成过滤使用映射后的节点号。无本地 Token 的 rank 可以执行远端专家工作；无 Token、无专家工作的成员使用空图正常完成。原单层参考输入及连续图生命周期语义保留。

新增参数对照、独立字节核算、已验证命令和保留边界见 [M6 样例](../fixtures/m6_execution_parameters/README.md)。该首项任务限单层固定轮次；后续服务、本地性及场景参数化结果见下文。

## M6 服务模型与容量参数化

`cosim-service` 根据 deployment 中的实际成员和端点映射建立计算、应用与控制器对象，按逻辑 rank 查图、按实际节点查 UB 回调。映射必须在组间也保持唯一，并指向已有计算端点；每轮计划中的映射和放置必须与部署一致。结果的 `rank_ids/rank_to_ub_node` 给出资源数组顺序，传输另记录物理源/目的节点；观测公开字段未改变。

新增 Python `moe-ub-configured-service/1` 输入，用完整声明模型与实际专家位置核算各 rank 权重、KV、工作区和通信缓冲区。接纳前在原生单一块池预留 `output - 1` 对应块数，原 Scheduler 继续选批、记账和释放。其容量初始化适配在主仓库，不修改锁定依赖。

当前该输入执行一组、一个选定 MoE 层，每 rank 一个请求；容量清单中的稠密层及其他 MoE 层不被替换或执行，清单明确标注未执行范围。旧服务/P/D/多层输入继续原行为。具体字段、容量公式、两个功能模型和已验证命令见[服务模型样例](../fixtures/m6_service_model/README.md)；MLA、同芯片本地性、连续组批与完整场景不在本步完成范围，计算和网络模型仍为未校准参考值。


## M6 部署本地性分类

layer v2 显式声明 die/chip/server 归属及本地准备参考时间。计划加载器从路由独立重建三类贡献，核对本地准备计算和交付依赖；`UbNetworkAdapter` 分别登记真实 UB 写与 `LocalDelivery`。后者没有 UB app/task，源准备计算完成后发布数据，通过 ns-3 同时刻排队的回调完成双方等待，晚到 RECV 仍只完成一次。

单层、固定两轮和配置服务均已执行验证；部署在一次运行内固定，本地交付参与图回收与死锁检查。运行结果新增 `local_deliveries`，`transfers` 和端口来源侧表继续只含实际 UB 写。样例、字段版本、完成条件、手算结果和命令见[本地性说明](../fixtures/m6_locality/README.md)。旧服务/P/D/多层保持原输入边界，完整场景与硬件校准尚未完成。

## M6 场景与后端参数适配

新增 `python -m moe_ub_cosim.scenario resolve|run` 小型场景入口，引用现有配置服务和显式网络，选择参考规格中的 WRITE/RTP 参数。`cosim-service --backend-catalog` 从当前注册的 TypeId/GlobalValue 导出目录，`--check-backend FILE` 校验完整选择；新网络目录的 `backend.json` 替代 TXT 加载路径。没有该文件的旧案例仍执行原入口。

运行前读取实际网络对象属性与节点/链路覆盖并核对，动态 Jetty 的 TA/inflight 在 WQE 创建时核对。结果分别保存集合 chunk、TA 段与 TP 编译常量、参数来源、实际对象、RNG、观测和固定依赖。只接受现有 URMA WRITE/RTP 的就绪与资源释放语义；READ、CTP、native LD/ST、CC/故障/重传及未经验证的多路径/喷洒选择均拒绝。范围、覆盖顺序、实际检查和命令见[场景样例](../fixtures/m6_scenario_backend/README.md)。

## M7 同 rank 多请求连续批处理

`moe-ub-configured-service/2` 配合 layer/3，在一组、一个选定 MoE 层内执行多请求纯 Decode。空闲迭代边界按 FCFS 和序列/KV 预算接纳，原 Scheduler 按 Token 预算选批；真实 UB 轮次完成后逐请求推进、完成和释放。暂时不足时等待，未选中的驻留请求仍占 KV，无法最终容纳的请求在启动前拒绝。

新后端部署为 `moe-ub-service-deployment/3`，携带一次运行固定的 batch_limits。`moe-ub-execution-plan/3` 保存逐请求上下文、dtype、批次规模和逐专家输入量；C++ 独立重建并检查，同一请求不能在一轮重复出现。图仍按真实批次生成，发送端只提交一份业务传输，接收端只登记等待；每 rank 一张活动图、一个计算槽及原 WRITE/RTP 完成语义均保留。

旧 schema 的单请求限制继续生效。新 schema 的来源记录不改变业务字节或时序，物理部署可保留同芯片路径；场景 YAML 可以引用新配置。线性参考耗时仍不随上下文长度或相同总量下的专家分布变化，不能视为目标计算模型校准。输入、预算、手算预期、实际 15 次运行与 11 项拒绝检查见[M7 样例](../fixtures/m7_continuous_batching/README.md)。M8 组合执行见下文。

## M8 多层、多请求、多组联合执行

configured-service/3 引用 layer-segments/1，各组分别声明完整模型容量、序列/Token 预算和逐层静态放置。后端 deployment/4 固定各组 batch_limits 与层配置，加载 multilayer-plan/2 时独立重建每层及合并图，再检查提交与部署一致。旧服务、P/D 和多层 schema 保持原入口。

每组的一次选批生成连续 MoE 层段的合并图，同一 rank 的后层入口依赖前层末端；空层保留已有依赖，没有全组逐层屏障。层间移动局部节点号，并为本地交付与真实 UB 写分配运行内唯一身份。本地交付不需要 UB task；跨 chip 的就绪、事务完成和回调继续由实际 WRITE/RTP 事件产生。完整层段结束后才报告 WAVE_DONE，各组独立选下一批。

端侧记录从每层的实际节点与本地依赖读取时间；来源信息不进入业务包。场景入口对所有层应用 chunk/发送槽配置，并要求预建通道覆盖每组成员对。通信缓冲按各层保守界求和，覆盖不同 rank 处于不同层的情形；KV 仍使用每 rank 唯一原生块池。

两个三成员组、三层、七请求的真实共享网络运行及 15 项正常对照、11 项拒绝检查见[M8 样例](../fixtures/m8_composed_service/README.md)。本步限相同模型结构、相同连续 MoE 层段的就绪 KV Decode；完整容量与选定执行范围分别记录。真实路由、计算 profile 和完整目标场景未在本步完成。

## M9 观察片段与算子 profile

`moe-ub-evidence-service/1` 从带摘要的路由 manifest 选择每请求连续片段，复用 M8 多组层段、原生 Scheduler 和唯一 KV 池。请求真实目标未知时输出 null；片段结束经过实际完成回调后记录 OBSERVATION_ENDED 及单独的 KV 清理，不伪造 EOS、正常请求完成或完整模型 latency。service_finished 只表示声明的观察工作负载及网络排空结束。

layer/3 的可选 operator_profile 使用实际上下文列表和逐专家输入向量精确查表。C++ 独立重算 domain、原始样本中位数、形状、零工作、节点时间及依赖，部署合同继续固定每层 profile；范围外输入明确拒绝。专家实测项替换旧线性项，attention 实测项与明确的准备残差相加，网络等待仍只来自本次 UB 事件。旧输入没有此字段时保留原行为。

格式、功能命令、测量范围及真实来源文件清单见 [M9 说明](../fixtures/m9_evidence/README.md)。两份 R1-AWQ 文件已核验，选定路由经过同一后端的两批次、三层实际 UB 重放；该对照仅含 routed 层通信与参考计算，不验证完整 R1 的 MLA、共享专家或容量。人工服务检查、真实路由、A100 测量和目标 NPU 校准分别记录；M9 整体验收状态以 [current.md](../docs/tasks/current.md) 为准。

## M10 固定 CANN 路径的小型执行

layer/3 可选择 `cann_a3_v850_fullmesh_v1` 和固定 revision 的 cann_profile。Python 生成逐专家数据和状态消息，C++ 的 cann_plan.cc 独立重建贡献、布局、缓冲及全部依赖；子层校验继续用于 multilayer-plan/2 和 deployment/4，不新增服务调度器或网络实例。场景 YAML 仍显式选择已有 WRITE/RTP profile。

同 chip 不同 rank 保留本地交付，跨 chip 消息按各自实际复制长度分块。Dispatch 计数发布等待本 rank 数据发送完成；专家计算等待数据/计数及重排，Combine 每份结果之后发布标志；每 lane 及发送槽等源事务完成才回收。端侧 Dispatch 结束包含新重排阶段，Combine 结束包含 Token 所属 rank 的归约。局部屏障不会扩展为全 rank 逐层屏障。

WriteDescriptor 可携带业务、应用元数据和填充的分段范围。观测始终被动读取任务至包的偏移，仅业务区间进入操作归属；来源关闭时仍正确分类原始载荷，不把状态 WRITE 算作协议控制包。旧计划的整个载荷仍为业务。研究导出兼容本地交付，并取消旧 0～11 rank 编号白名单，与执行计划范围一致，公开字段不增加内部身份。

源码、参考近似、缓冲公式、限制、实际命令和检查见 [M10 样例](../fixtures/m10_cann/README.md)。未执行 CANN/NPU，不支持原生 MTE/LDST 等价声明；当前模板拒绝阶段/形状不匹配的 operator_profile。完整 M10 和目标规模验收尚未完成。

## M10 共享观测来源

`cosim-service` 为一次有观测的运行创建一个 `WriteProvenance`，每轮只登记一次传输。所有端口共享其描述符和包范围账本，源 TP 只监听一次；端口窗口、队列及来源输出独立。旧单端口构造入口内部创建自己的单份账本。共享对象不持有 TP，包回调只持有账本，保留原生 TP 回收与晚到回调安全性。

输入和公共观测 schema 不变。内部 `provenance-audit.json` 与各端口新增资源字段区分物理表条目和逻辑可见数量，不能把各端口 `registered_transfers` 相加当作实际驻留副本数。账本数量预算取各端口最严格值，当前仍累计保留本次运行记录。新旧后端对照及复现入口见[共享来源验收](../fixtures/m10_shared_provenance/README.md)；完整目标资源与多轮回收另行处理。

## 离线图加载测量

`cosim-plan-load-probe GRAPH_DIRECTORY` 调用与服务相同的 `LoadLayerPlan`，保留 JSON、全模型重构和 Chakra 校验，输出实际节点及 fabric/本地描述符数量。它不创建网络或 ASTRA 活动图，也不推进仿真；须由 `moe_ub_cosim.host_probe` 施加宿主资源上限。构建目标为 `cosim-plan-load-probe`，测量与适用边界见[完整一轮预检](../fixtures/m10_full_round_preflight/README.md)。小图成功不能替代目标完整图装载证据。
