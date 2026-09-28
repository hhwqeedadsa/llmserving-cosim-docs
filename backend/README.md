# M1 最小联合程序

本目录的 `cosim-compute` 用锁定 ASTRA 的 Sys、Workload、HardwareResource 和 Chakra reader 执行一个合成 COMP。计算完成回调由 ns-3-UB 的事件队列触发。M1.2 新增 `cosim-write`，执行一个实际 ASTRA SEND 和一份 UB 写事务，并从接收适配器登记等待。未配置传输描述的计算适配器仍拒绝收发。

从主仓库根目录操作。构建入口是 ns-3-UB，`backend/inject.cmake` 复用 M0 的核心库边界并加入计算目标；不能直接以本目录作为 `cmake -S`。完整的已执行配置、构建、测试命令及日志见 [M1.1 环境记录](../docs/environment.md#2026-09-28m1-1-compute-time)。

本机已构建的程序调用形式：

```text
build/m1-compute/output/cosim/ns3.44-cosim-compute INPUT.json SYSTEM.json NEW_RUN_DIR
```

功能输入见 [comp_1000ns.json](../fixtures/m1_compute/comp_1000ns.json)，ASTRA 配置见 [system.json](../fixtures/m1_compute/system.json)。运行目录必须不存在，其父目录必须已存在。程序保存输入副本、生成的 `compute.0.et`、ASTRA 执行日志和 `result.json`，不覆盖已有运行。

`start_ns` 是绝对起点，`duration_ns` 是非负整数持续时间。字段只接受 `moe-ub-compute/1`，并在写图前检查相加结果不超过 ns-3 的有符号 64 位纳秒范围。锁定 Chakra 分支把纳秒写入名为 `duration_micros` 的历史字段；本程序按该分支读取器的实际行为直接写入，不再乘除 1,000。零 runtime 且零 num_ops 的 COMP 由原 Workload 当作零时间节点移除，不占计算资源。

`UbNetworkAdapter::sim_schedule` 只接受锁定 Sys 提供的 NS 单位，正的小数纳秒向上取整，零也通过异步事件回调；负数、非有限值、错误单位、空回调和时间溢出均拒绝。`sim_get_time` 返回 ns-3 的绝对纳秒值，`sim_notify_finished` 报告完成而不停止其他事件。适配器及回调参数必须存活到相关事件执行完毕；本入口在事件队列排空后销毁 Sys 和适配器。每个进程只在事件运行前设置一次 ns-3 时间精度。

计算入口、时间适配器和 UB 使用 C++20；ASTRA 及独立的日志桥接使用 C++17。日志桥接隔离锁定日志头的 syslog 宏及 C++20 `format()` 歧义，不修改上游依赖。

检查入口为 `cosim-time-test`（时间边界）、它的 `--wrong-resolution` 独立进程，以及 `tests/integration/check_compute.py`（四项计算、非法输入、原 ASTRA issue/callback 日志、资源与库来源）。M0 原链接探针仍保留。配置会重写 UB 忽略的启动锁，操作前必须备份已知原文件，结束后恢复并检查原生关键产物。

这些结果只验证功能与时间接口。固定计算耗时和网络参数没有目标硬件校准依据。

## M1.2 单份写事务

从主仓库根目录运行已构建入口（最后一个参数必须为新目录）：

```bash
build/m1-transfer/output/cosim/ns3.44-cosim-write \
  fixtures/m1_transfer/single_write.json fixtures/m1_compute/system.json \
  third_party/ns-3-ub/scratch/2nodes_single-tp runs/my-write
```

程序复制网络配置、节点、拓扑、路由和 TP 配置，通过 UbUtils 创建两端设备及交换机，不读取或生成 `traffic.csv`。源 rank 0 的 Chakra 图含一个 SEND；rank 1 的适配器直接登记配对等待。本步没有执行 ASTRA RECV 图，也没有触发 `data_ready`，运行结果必须报告 `receiver_waiting=true`、`receive_callbacks=0`。

`UbWriteTransfer` 保存一份描述和回调状态，检查 `(src_rank, dst_rank, tag, bytes)` 及 UINT8 类型。`transfer_id` 是内部 64 位标识，`task_id` 显式映射为 UB 的 32 位任务号；字节数也必须在 UB 的 32 位范围内，本步拒绝零字节。`tag` 限于 ASTRA NATIVE 不重映射的范围。业务优先级固定为原样例的 7，内部图和节点标识不参与选路。锁定 ASTRA 的 RECV 栈请求未初始化全部字段，因此本接口只保存显式实参所需信息，不读取或保留 `sim_request*`。

UB 补丁中的 `UbTransferExecutor` 保存任务值和完成接收者，复用 UbApp 原协议提交代码；它只复用 `RuntimeTask` 值类型，不查询 CSV 任务表或 phase。CSV 调用继续通知 UbTrafficGen，联合调用通知适配器。WQE 完成后安排同一仿真时刻的清理事件，退出 Jetty 内部回调栈后再释放 RTP Jetty、双向绑定和事务元数据；TP 保留或回收仍由原属性控制，CTP 共享 Jetty 保留。清理后再异步调用发送回调，`sender_completion` 固定为 `transaction_done`。适配器、描述及回调参数必须活到物理完成与回调执行结束。

UB 原交换机永久周期调度死锁检查，测试入口在 `start_ns + 1,000,000 ns` 停止；这只是功能测试的运行上限。实际事务必须在上限前完成，发送槽与执行上下文清空，否则检查失败。rank 完成通知不停止仿真，测试另安排后续事件验证这一点。联合程序使用 NS 精度，原 CSV 入口保留其 PS 精度；不直接将两种入口的完成数值混作同一个基准。

主例在 10 ns 提交 409,600 字节，在 8,622 ns 由真实 UB 完成事件释放发送槽。两种接口登记顺序及 TP 回收配置给出相同完成时刻；1 字节例在 188 ns 完成。构建、接口单元、写事务、计算和原 CSV 对照命令见 [M1.2 环境记录](../docs/environment.md#2026-09-28m1-2-write-transfer)。`check_csv_regression.py` 需要保留的原版二进制作为参照，不会自行检出、升级或重编译依赖。

M1.3 才处理目标端实际 `data_ready`、接收回调和完整双端图。本步结果不表示 M1 整体或模型校准完成。
