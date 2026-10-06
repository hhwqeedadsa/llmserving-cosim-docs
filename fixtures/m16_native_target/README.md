# 原生完整目标首轮验收

最终检查见 [result.json](result.json)。完整原生首轮、封存、公开采样、观测校验和独立字节核算均通过；执行状态统一维护于[当前任务](../../docs/tasks/current.md)。[preflight.json](preflight.json) 与 [feeder-lifetime.json](feeder-lifetime.json) 保留此前的预检、失败及修复检查点，其中当时的“完整验收未完成”不代表最终结果。

## 输入和结果范围

本例保留 [CM384/R1 参考场景](../../docs/scenarios/cm384_r1.md)的 128 rank、1024 个独立真实请求来源、61 块、七平面完整物理网络，以及首个 Decode 入口和参考计算成本。重新生成原生图：Dispatch 向量使用 STORE，Combine 向量使用 LOAD；应用状态和每段 LOAD 的 32 B 供数通知使用真实 STORE。没有从旧 WRITE 图替换枚举，也没有复用历史包时刻。

| 检查对象 | 实际结果 |
|---|---:|
| 交付总数 / 网络 / 本地 | 4,703,152 / 4,677,222 / 25,930 |
| 完成的图节点 | 9,526,154 |
| 唯一网络业务量 | 10,058,044,416 B |
| LOAD 数据 / STORE 供数通知 | 各 935,458 次 |
| 网络物理包发出 / 到达 | 各 46,058,033 个 |
| 模拟结束时刻 | 48,446,098 ns，即 48.446098 ms |
| 端侧操作 / 阶段记录 | 14,848 / 143,379 |
| 原始压缩记录 | 953,471,576 B |

正常退出时网络空闲，128 个 rank 的发送槽、任务、事件和回调，以及图、适配器、原生请求和来源状态均已回收。独立检查程序 [check_native_target_result.py](../../tests/integration/check_native_target_result.py) 流式核对交付计数、LOAD 发起与供数方向、唯一业务字节、有效载荷包数、五类消息来源及所选端口覆盖。

这是从 KV 已就绪初态执行的完整首轮图，不是完整 64 轮服务、请求调度器全程执行或目标硬件校准。完整目标使用生产重放驱动在新网络中执行一次；本次没有再运行第二次完整目标重放。相同输入重放等价、改变服务区间的实际时序变化已由下述独立小例检查。

## 明确选择的参数和观测

[config.json](config.json) 选择每端点一个原生 worker，STORE 请求/LOAD 响应粒度 4096 B，每 worker 最多 32 个未完成请求，每发起方最多 256 个线上请求编号；每 rank 一个逻辑发送槽、向量分块 8192 B，seed/run 均为 1。使用最短路径候选、逐包喷洒和 CBFC；没有新增多 TP。一个 worker 和 4096 B 是影响包数、时序的模型参数，不代表目标硬件实测值，也不是保持网络时序不变的宿主优化。

观察 42 个物理出口：每平面在服务器 0、7 的 L1 各取设备口 0 和上行口 16，并取该平面第一个 L2 的服务器口 0、7。全部所选端口都有实际流量，覆盖七平面的所选 L1/L2 方向，未记录其余端口。基础窗口 100 µs，声明 `[0,1 s)`；只有 L1 节点 768 出口 16 的前 100 µs 保留详细事件。五类应用消息真值、操作阶段和 VOQ/FIFO 等待统计均开启。公开导出含 420,000 个窗口，其中 399,803 个无计数包；队列非零窗口为 20,197 个。本例没有出现无发包的非零等待窗口，该边界由 [M14 小用例](../m14_operation_queue/README.md)单独验证。

算法读取 `observed/`，评价用分类来源保存在 `truth/`。仅有聚合资料的范围允许在相同时钟下对齐合并窗口；不能任意细分、移动边界或修改漂移。仅在完整保留相应详细事件和队列状态的范围内才能改变这些条件；其余情况需重新执行。实际结束后的空闲尾部不表示还在执行推理。能力声明通过 `slice inspect` 查看，超出范围的采样在生成输出前拒绝。

## 公开复用入口

当前机器的产物位于 `runs/v04-platform/`，大文件未随 Git 分发：

| 路径 | 内容 |
|---|---|
| `m16-target-slice/` | 公开首轮数据包；压缩图、执行资料、输入与摘要 |
| `m16-target-package/` | 校验后的归档、导入与采样配置；`materialized/raw/` 为有界采样缓存 |
| `m16-target-sample/` | 已通过校验的观测、分类真值、导出清单与资源日志 |
| `m16-full-native-feeder/work/` | 实际网络输入、结果和资源退出摘要；展开图和原执行目录已核验封存 |
| `m16-target-delivery/` | 公开命令、独立检查的退出状态及 stdout/stderr |

安装按[用户指南](../../docs/user_guide.md)执行。以下使用已有完整数据包，不调用前端构图或网络后端；新输出目录必须不存在：

```bash
moe-ub slice inspect --slice runs/v04-platform/m16-target-slice --verify
moe-ub sample --slice runs/v04-platform/m16-target-slice \
  --config runs/v04-platform/m16-target-package/sample.json \
  --output runs/v04-platform/my-native-sample
moe-ub validate --run runs/v04-platform/my-native-sample
moe-ub validate --observed runs/v04-platform/my-native-sample/observed
```

实际验收使用同一 CLI 的 `python -m moe_ub_cosim` 入口，命令和退出状态见 `m16-target-delivery/commands.json`。迁移机器需取得完整数据包，并调整采样配置中的本机缓存和预算路径；不得把本机 `runs/` 路径当作已分发的输入。重放的配置与覆盖边界见用户指南，完整规模重放须另做展开缓存与执行预算检查。

不依赖外部账户或历史产物的操作入口是[原生首轮示例](../../examples/native_first_round/README.md)。自动检查 [check_v04_workflow.py](../../tests/integration/check_v04_workflow.py) 在新目录执行 `resolve/run/slice/sample/replay/validate` 共 14 条公开命令；相同重放的表逐项相同，定向限速将模拟结束时刻从 47,193 ns 改为 75,919 ns，不支持的采样被拒绝。WRITE 用户流程与旧详细首轮兼容检查也通过，证据分别见 [feeder-lifetime.json](feeder-lifetime.json)和 [M15](../m15_aggregate_capture/README.md)。

## 生成与资源证据

构图来源是 M10 已逐文件核验的首入口 bundle，固定摘要后保留全部路由、成本、层和部署，再新增明确的原生映射。来源内容和账户不随 Git 提交，本轮没有下载数据。重新构图需取得相应原始输入或已核验 bundle；可用 `scripts/prepare_native_target.py --help`、`scripts/run_native_target.py --help` 查看专用验收入口。前者只准备完整或前缀输入，后者执行声明的首轮；研究实验应使用独立配置与公开平台入口。

`result.json` 保存输入/代码/二进制摘要及每阶段实际命令、目录、退出状态和日志位置。完整构图使用 `full_round_probe target --compact-plan`；构图与 C++ 仍执行完整语义、字节、依赖和 Chakra 校验。首次资源脚本目录假设错误、首次完整后端超出内存及检查命令参数错误均保留原因和原日志，详见两份历史检查点。

| 阶段 | 宿主耗时 | 峰值 RSS（GB，十进制） | 退出 |
|---|---:|---:|---:|
| 完整构图和写盘 | 1,729 s | 41.02 | 0 |
| 完整网络准备、执行及退出 | 4,358 s | 48.89 | 0 |
| 校验封存与回收 | 294 s | 0.065 | 0 |
| 公开数据包创建 | 40 s | 0.060 | 0 |
| 检查、采样及独立核对合计 | 569 s | 0.315 | 0 |
| 其中离线采样 | 464 s | 0.281 | 0 |

RSS 取进程树采样和退出时子进程高水位中的较大值；离线采样已包含在上一行，不重复求和。时间不含早期预检和失败调查。完整执行限 RSS 48 GiB、地址空间 56 GiB、7200 s；小型与导出阶段限 RSS 8 GiB、地址空间 12 GiB、1800 s。六个登记的新旧 build/run 根累计限 32 GiB，并保持文件系统至少 20 GiB 空闲。

首次完整后端在模拟时间 0 尚未激活业务时达到 54.81 GB，被监控停止。修复将 Chakra 执行读取器的创建推迟到传输 JSON 已归档释放之后，保留全部校验；原前缀所有执行字段和 42 端口记录完全一致。随后使用同一完整图、原预算完成执行，未提高限额。

实际图 12,303,611,246 B，核验归档后为 866,814,095 B；执行目录 1,220,057,287 B，归档为 886,018,245 B。维护程序 [archive_v04_graph.py](../../scripts/archive_v04_graph.py) 与 [seal_native_target.py](../../scripts/seal_native_target.py) 校验每个归档成员的大小、摘要和显式登记副本后才回收。全过程累计峰值 34,072,469,504 B（31.73 GiB），最终采样检查时为 22,914,506,752 B（21.34 GiB），包含旧 M10 登记占用及当前采样缓存。未清理旧 M10 数据。
