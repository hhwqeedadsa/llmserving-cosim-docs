# 仿真引擎优化 PR 说明

## 先看数字 🚀

这几个数最能说明这次改动的收益：

| 指标 | 对照 | 结果 | 变化 |
|---|---:|---:|---:|
| 🧠 1k allreduce ring 峰值 RSS | 2,556,657,664 B | 199,294,976 B | 内存下降约 92.2% |
| ⚡ 1k allreduce ring 总 probe 耗时 | 103.44s | 0.77s | 约 134x 加速 |
| AddTask 依赖构建耗时 | 101.589s | 已并入 0.77s 总耗时 | 原来的主要热点基本消失 |
| 🧵 256 host Clos 随机跨 leaf，20k 流，MTP8 run 阶段 | 单线程 13.31s | MTP8 4.96s | 约 2.69x 加速 |
| 1k traffic records | 1,998,000 | 1,998,000 | 输入规模一致 |
| phase 依赖引用数 | 1,997,000 | 1,997,000 | 语义一致，不靠删依赖取巧 |
| 复杂 Clos 业务输出 | main baseline | 当前分支 | 字节级 MATCH |
| MTP 4 线程 Clos 业务输出 | main baseline | 当前分支 | 字节级 MATCH |

原来的 1k allreduce ring 主要卡在 `AddTask`：光依赖构建就用了 100s 以上，RSS 超过 2.5GB。现在同一份 traffic 的载入和建模整体不到 1s，RSS 约 190MB。输入 records 和 phase 依赖引用数都没变，省下来的部分来自运行期建模方式，不是把依赖删掉了。

小规模 MTP case 里启动和 trace 成本占比太高，差异不明显。补了一组更重的 256 host Clos 随机跨 leaf 流量后，MTP8 的 run 阶段从 13.31s 降到 4.96s，能看出多线程调度收益。

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

## 复杂 MTP 补测

原来的 `2nodes` 和 32 host Clos case 太小，MTP 差异容易被启动、配置读取、trace 写入和 trace 解析盖住。为避免只看固定成本，这里补了一个 256 host 两层 Clos：

- 拓扑：256 host，16 leaf，16 spine，链路 400Gbps / 20ns
- workload：20,000 条随机跨 leaf `URMA_WRITE`，每条 64KB，无 phase 依赖
- 计时方式：关闭 trace 和 parse，只看仿真调度 run 阶段
- 测试对象：main `dcb3c99` 与 PR HEAD `b12d398`

| 模式 | main run | 当前分支 run | main total | 当前分支 total | main RSS | 当前 RSS | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| 单线程 | 12.620566s | 13.312636s | 12.703508s | 13.410281s | 1,236,303,872 B | 1,216,282,624 B | 同量级，当前分支略慢 |
| MTP 8 线程 | 5.149204s | 4.956796s | 5.243586s | 5.040523s | 1,381,974,016 B | 1,353,809,920 B | 当前分支 MTP8 快约 3.7%，RSS 低约 2.0% |
| 当前分支：单线程 vs MTP8 | 13.312636s | 4.956796s | 13.410281s | 5.040523s | 1,216,282,624 B | 1,353,809,920 B | MTP8 run 阶段约 2.69x 加速 |

这组数主要说明两点。第一，小 case 看不出 MTP 效果不代表 MTP 没效果，20k 随机跨 leaf 流量下 run 阶段差异已经很明显。第二，本 PR 相比 main 的 MTP8 改动收益是小幅收益，不是 2.69x 的来源；2.69x 是“当前分支单线程”和“当前分支 MTP8”的对比。

另补一组 trace-on 的字节级一致性验证：

- 拓扑：同样是 256 host Clos
- workload：2,000 条随机跨 leaf `URMA_WRITE`，每条 64KB
- trace：开启常规 task / packet / port trace 和 parse，关闭 `UB_RECORD_PKT_TRACE`，避免 `AllPacketTrace_*` 的 `Uid` 噪声
- 模式：main MTP8 vs 当前分支 MTP8

| 输出 | 结果 |
|---|---|
| `test/` | MATCH，2 个文件字节级一致 |
| `runlog/` | MATCH，1,568 个文件字节级一致 |
| `output/` | MATCH，无输出文件差异 |

这组 trace-on case 的性能不是本 PR 的性能主张：main run 0.902286s，当前分支 run 1.126618s。它只用于确认复杂 MTP 路径没有破坏输出。

50k 随机跨 leaf 流也试过，但 main 的 MTP8 进入长时间 no-progress 段，日志膨胀且没有在合理时间内完成，所以没有把它作为 PR 性能数据。

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
