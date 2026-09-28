# M1.1 单节点计算程序

本目录的 `cosim-compute` 用锁定 ASTRA 的 Sys、Workload、HardwareResource 和 Chakra reader 执行一个合成 COMP。计算完成回调由 ns-3-UB 的事件队列触发。当前不执行 UB 业务收发；适配器的 SEND/RECV 调用会明确报错。

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

这些结果只验证功能与时间接口。固定耗时没有目标硬件校准依据；UB 写任务、接收等待及两侧完成事件留待后续已确认任务。
