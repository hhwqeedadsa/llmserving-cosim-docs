# 仿真引擎优化 PR 说明

## 先看数字 🚀

这几个数最能说明这次改动的收益：

| 指标 | main | 当前分支 | 结果 |
|---|---:|---:|---:|
| 🧠 1k allreduce ring 峰值 RSS | 2,556,657,664 B | 199,294,976 B | 内存下降约 92.2% |
| ⚡ 1k allreduce ring 总 probe 耗时 | 103.44s | 0.77s | 约 134x 加速 |
| AddTask 依赖构建耗时 | 101.589s | 已并入 0.77s 总耗时 | 原来的主要热点基本消失 |
| 1k traffic records | 1,998,000 | 1,998,000 | 输入规模一致 |
| phase 依赖引用数 | 1,997,000 | 1,997,000 | 语义一致，不靠删依赖取巧 |
| 复杂 Clos 业务输出 | main baseline | 当前分支 | 字节级 MATCH |
| MTP 4 线程 Clos 业务输出 | main baseline | 当前分支 | 字节级 MATCH |

原来的 1k allreduce ring 主要卡在 `AddTask`：光依赖构建就用了 100s 以上，RSS 超过 2.5GB。现在同一份 traffic 的载入和建模整体不到 1s，RSS 约 190MB。输入 records 和 phase 依赖引用数都没变，省下来的部分来自运行期建模方式，不是把依赖删掉了。

## 改动范围

这次 PR 统一按“仿真引擎优化”收口，主要包含两部分：

| 模块 | 具体改动 | 目的 | 兼容性策略 |
|---|---|---|---|
| `UbTrafficGen` | 将运行期任务从完整 `TrafficRecord` 改成紧凑 `RuntimeTask` | 降低每个任务常驻内存 | 保留 `LoadTrafficConfig()`、`AddTask()` 等旧接口 |
| `UbTrafficGen` | 依赖从“任务级 DAG 边”改成“phase 完成计数 + dependent task 列表” | 避免 allreduce/ring 等场景形成海量重复依赖边 | 不绑定 allreduce/ring，不假设 traffic pattern |
| `UbTrafficGen` | 两遍流式读取 `traffic.csv` | 避免把全量 CSV 任务和依赖字符串常驻内存 | 只多一次顺序文件扫描 |
| `UbTrafficGen` | taskId 连续时走 dense vector，稀疏 taskId 自动 fallback 到 map | 降低哈希表和节点分配开销 | 兼容非连续 taskId |
| `UbTrafficGen` | ready task 从 `std::set` 改成 vector + 必要时排序去重 | 降低调度热路径容器开销 | 保持 taskId 升序调度 |
| `UbTrafficGen` | priority / uint32 字段显式校验 | 明确不支持有符号数字，避免隐式解析歧义 | 非法输入 fail-fast |
| MTP | mailbox 加锁、原子状态、busy wait `CpuRelax()`、hybrid local system id 修正 | 降低多线程竞态和自旋开销 | 保留旧 per-sender remote event tie-break |
| MPI + TrafficGen | `traffic.csv` 多进程路径显式 fail-fast | 避免误以为分布式 operator 依赖已支持 | main 和本分支均为 fail-fast，不是本 PR 新回归 |

## TrafficGen 1k allreduce ring 实测

输入为同一份 1k allreduce ring traffic：

`/tmp/ub-trafficgen-memory/20260629162412_1000_1000_1048576_ar_ring_d0_linear/traffic.csv`

| 指标 | 当前 main `dcb3c99` | 当前分支 | 变化 |
|---|---:|---:|---:|
| traffic 行数 | 1,998,001 | 1,998,001 | 一致 |
| records | 1,998,000 | 1,998,000 | 一致 |
| dependOnPhaseRefs | 1,997,000 | 1,997,000 | 一致 |
| load/index 阶段耗时 | load 0.775s | index 0.130s + load 0.495s | 0.775s -> 0.625s |
| AddTask / 总 probe 耗时 | add 101.589s，总 103.44s | 总 0.77s | 约 134x 加速 |
| `/usr/bin/time -l` max RSS | 2,556,657,664 B | 199,294,976 B | 降到约 7.8% |
| probe `ru_maxrss` | 2,556,657,664 B | 199,294,976 B | 降到约 7.8% |

main 兼容 probe 日志：

`/tmp/ub-trafficgen-memory/main-origin-current-compat-120s.log`

当前分支 probe 日志：

`/tmp/ub-trafficgen-memory/current-final-after-mtp.log`

## main 对比验证矩阵

对照版本：

| 目标 | commit |
|---|---|
| main | `dcb3c99a6bb8ac9392e7f929e29e20b17f9612d2` |
| 当前分支 | 验证代码为 `d847a46` 加 MTP 收尾改动；最终代码以 PR 分支 HEAD 为准 |

验证脚本：

`/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/run_matrix.sh`

结果目录：

`/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/results`

| 模式 | 场景 | main 状态 | 当前分支状态 | 业务输出字节级 | runlog 字节级 | main 耗时 | 当前耗时 | main RSS | 当前 RSS |
|---|---|---:|---:|---|---|---:|---:|---:|---:|
| 单线程 | `scratch/2nodes_single-tp` | 0 | 0 | MATCH | MATCH | 0.226s | 0.313s | 29,376,512 B | 29,818,880 B |
| 单线程 | `scratch/clos_32hosts-4leafs-8spines_pod2pod` | 0 | 0 | MATCH | MATCH | 0.253s | 0.288s | 79,233,024 B | 79,691,776 B |
| MTP 4 线程 | `scratch/2nodes_single-tp` | 0 | 0 | MATCH | 原始 DIFF，归一化 `Uid:` 后 MATCH | 0.194s | 0.202s | 29,736,960 B | 30,457,856 B |
| MTP 4 线程 | `scratch/clos_32hosts-4leafs-8spines_pod2pod` | 0 | 0 | MATCH | 原始 DIFF，归一化 `Uid:` 后 MATCH | 0.262s | 0.257s | 80,232,448 B | 80,756,736 B |
| MPI 2 进程 | `scratch/ub-mpi-minimal` | 1 | 1 | 无业务 artifact，stdout MATCH | 无 runlog | 0.633s | 0.115s | 19,709,952 B | 20,381,696 B |
| MPI 2 进程 | `scratch/clos_32hosts-4leafs-8spines_pod2pod` | 1 | 1 | 无业务 artifact，stdout MATCH | 无 runlog | 0.642s | 0.631s | 19,693,568 B | 20,348,928 B |
| MPI 2 进程 + MTP 2 线程 | `scratch/ub-mpi-minimal` | 1 | 1 | 无业务 artifact，stdout MATCH | 无 runlog | 0.106s | 0.106s | 19,742,720 B | 20,283,392 B |
| MPI 2 进程 + MTP 2 线程 | `scratch/clos_32hosts-4leafs-8spines_pod2pod` | 1 | 1 | 无业务 artifact，stdout MATCH | 无 runlog | 0.106s | 0.103s | 19,677,184 B | 20,217,856 B |

MTP runlog 的原始 DIFF 只出现在 `AllPacketTrace_*` 的 `Uid:` 字段：

| 场景 | runlog 文件数 | 原始 diff 文件数 | 归一化 `Uid:` 后 diff 文件数 |
|---|---:|---:|---:|
| `scratch/2nodes_single-tp` | 44 | 4 | 0 |
| `scratch/clos_32hosts-4leafs-8spines_pod2pod` | 138 | 32 | 0 |

说明：这个 `Uid:` 是 ns-3 `Packet::GetUid()` 的进程内全局计数，MTP 调度下本身不适合作为业务等价判断字段。`test/task_statistics.csv` 和 `test/throughput.csv` 均为字节级一致。

## 验证命令

```bash
python3.12 ./ns3 build -j 8 ub-quick-example

PATH="/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/.venv-verify/bin:$PATH" \
PYTHON="/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/.venv-verify/bin/python" \
CURRENT_REPO="/Users/ytxing/workspace/ns-3-ub-gitcode-trafficgen-dag-memory" \
MAIN_REPO="/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/main" \
/Users/ytxing/workspace/ns-3-ub-verify-trafficgen/run_matrix.sh

./build/utils/ns3.44-test-runner --test-name=unified-bus
```

单测结果为 `PASS unified-bus`。测试输出中的 fatal/abort 是测试套件故意触发的子进程边界检查。

## 已知边界

| 边界 | 结论 |
|---|---|
| MPI multi-process + `traffic.csv` / `UbTrafficGen` | main 和本分支都 fail-fast，stderr 明确说明当前不支持 distributed/operator-style synchronization via traffic.csv |
| MTP `AllPacketTrace_*` 原始 `Uid:` | 不作为业务字节级等价依据；归一化 `Uid:` 后 runlog 内容一致 |
| `clang-format` | 当前机器未找到 `clang-format` 可执行文件；已执行 `git diff --check`，无空白错误 |
