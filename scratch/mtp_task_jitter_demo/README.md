# MTP task start jitter regression demo

这是一个公开的小型合成用例，用来回归 MTP 同时间戳任务释放问题，并展示 task start jitter 的 seed 语义。用例由 `scratch/ns-3-ub-tools` 生成，不包含私有 workload 数据。

- 拓扑：8 hosts、4 leaf、2 spines 的两层 Clos。
- 流量：All-to-All scatter，2 个 4-rank 通信域，`scatter-k=2`，每 rank `1MB`。
- network attributes：从当前构建的运行时 parameter catalog 写入完整快照。
- 显式 override：`UbSwitchAllocator::AllocationTime=1ns`、
  `UbJetty::UbJettyInflightMax=10000`。
- trace：只保留 task trace，避免 packet/port trace 把示例变重。

重新生成：

```bash
python3.12 scratch/mtp_task_jitter_demo/generate_case.py
```

这个 case 有 phase dependency，下面的命令显式使用 `--dependency-visibility-delay=10ns`。

## 回归矩阵

不开 jitter：

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/mtp_task_jitter_demo --dependency-visibility-delay=10ns --canonical-output=/tmp/mtp_noj_single'
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/mtp_task_jitter_demo --dependency-visibility-delay=10ns --mtp-threads=4 --canonical-output=/tmp/mtp_noj_mtp4'
diff -q /tmp/mtp_noj_single.rank0.txt /tmp/mtp_noj_mtp4.rank0.txt
```

打开 `1ns` task jitter：

```bash
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/mtp_task_jitter_demo --dependency-visibility-delay=10ns --task-start-jitter-window=1ns --task-start-jitter-seed=1 --canonical-output=/tmp/mtp_jitter_single'
python3.12 ./ns3 run --no-build 'scratch/ub-quick-example --case-path=scratch/mtp_task_jitter_demo --dependency-visibility-delay=10ns --mtp-threads=4 --task-start-jitter-window=1ns --task-start-jitter-seed=1 --canonical-output=/tmp/mtp_jitter_mtp4'
diff -q /tmp/mtp_jitter_single.rank0.txt /tmp/mtp_jitter_mtp4.rank0.txt
```

同一 case 的实测结果：

| 代码与参数 | single vs MTP 4-thread |
| --- | --- |
| `origin/main`，jitter off | 不一致 |
| 当前修复，jitter off | 字节级一致 |
| 当前修复，`1ns`，seed 1 | 字节级一致 |
| 当前修复，`1ns`，seed 2 | 字节级一致 |

这说明当前 runtime 修复已经消除了这个公开 case 的默认漂移。task jitter 仍保留为显式的对比工具，不是让这个 case 通过所必需的开关。

## Seed 的影响

不同 seed 是不同的任务释放计划。当前修复下，`1ns` 的 seed 1 与 seed 2 各自都能让 single/MTP 一致，但两个 single 输出有 20/48 行不同。按通信域统计，时间单位为 ns：

| 通信域 | 同数据量 phase | seed 1 | seed 2 |
| --- | --- | ---: | ---: |
| ranks 0-3 | phase 0，2MiB，结束时间 | 16643 | 16646 |
| ranks 0-3 | phase 1，1MiB，开始时间 | 16653 | 16656 |
| ranks 0-3 | phase 1，1MiB，结束时间 | 22513 | 22515 |
| ranks 4-7 | phase 2，2MiB，结束时间 | 16643 | 16643 |
| ranks 4-7 | phase 3，1MiB，开始时间 | 16653 | 16653 |
| ranks 4-7 | phase 3，1MiB，结束时间 | 22512 | 22512 |

`1ns` 是每个 source 独立使用的窗口上限，不是给每个 task 固定增加 `1ns`。同一 source 的 task 按 seed 从 `0ps` 开始分配不同 slot，只有一个 ready task 的 source 不会被平移；不同 source 可以复用相同 slot 并保持并发。canonical 输出以整数 ns 记录，因此首批 task 都显示为 `10ns`，但亚 ns 的释放次序仍会改变后续 packet、phase 完成和依赖 phase 的开始时间。

如果同一批次中每个 source 都只有一个 ready task，jitter 不会改变这批任务，也不能给跨 source 的启动波排序。

不要把 task jitter 当成物理链路时延。使用它时，window 和 seed 都应写入实验参数；换 workload 后也要重新验证 single/MTP 是否一致。

## English notes

This is a public synthetic regression case generated with `scratch/ns-3-ub-tools`. It reproduced the original no-jitter single/MTP mismatch on the `origin/main` baseline. With the runtime fixes in this change, no-jitter single and MTP 4-thread output is byte-identical, so jitter is no longer required to make this case pass.

Task start jitter remains an opt-in comparison tool. It only spreads batches containing multiple ready tasks from the same source; one ready task per source is unchanged. With a `1ns` window, seed 1 and seed 2 are each byte-identical between single and MTP, while the two single-thread seed outputs differ in 20 of 48 canonical lines. For ranks 0-3, the dependent 1MiB phase starts at `16653ns` with seed 1 and `16656ns` with seed 2, then completes at `22513ns` and `22515ns`. The window and seed are model parameters, not physical link latency.
