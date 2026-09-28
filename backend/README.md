# M1 最小联合程序

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
