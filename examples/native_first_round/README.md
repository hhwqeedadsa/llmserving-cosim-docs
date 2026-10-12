# 原生 STORE/LOAD 首轮小例

本例演示原生内存传输、逐包喷洒、额外准备耗时、出口服务区间，以及聚合与选择性详细保存的组合。三个 rank 执行一个稠密块和三个 MoE 块；请求和专家选择为合成输入，无需 GPU、模型权重、真实路由账户或历史产物。

先完成[安装和构建](../../docs/user_guide.md)，在仓库根目录执行。输入路径相对配置文件，命令路径相对当前目录；每个输出目录都须尚不存在。自选构建目录时替换 `--backend`：

```bash
moe-ub resolve --config examples/native_first_round/scenario.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service --output runs/native-input
moe-ub run --config examples/native_first_round/run.json --output runs/native-run
moe-ub validate --run runs/native-run
moe-ub slice create --from-run runs/native-run --output runs/native-slice
moe-ub slice inspect --slice runs/native-slice --verify
moe-ub sample --slice runs/native-slice --config examples/native_first_round/sample.json \
  --output runs/native-sample
moe-ub replay --slice runs/native-slice --config examples/native_first_round/replay.json \
  --output runs/native-replay
moe-ub validate --run runs/native-replay
```

| 输入 | 用途 |
|---|---|
| `scenario.yaml`、`backend-reference.yaml`、`fabric.json` | 原生 Dispatch STORE / Combine LOAD；两个平面、最短路逐包喷洒、CBFC、三个 worker、请求并发与编号上限；节点 4 出口 4 在 `[0,100000)` ns 限为 1 Gbit/s |
| `service.json`、`layers.json`、`system.json` | 模型、请求、专家、计算成本、真实 LOAD 供数通知；rank 2 的 Dispatch 与 rank 12 的 Combine 额外准备成本 |
| `run.json` | 指向解析后的输入，并声明有限宿主时间、内存和磁盘 |
| `observation.json` | 五个出口的 127 ns 窗口，VOQ/FIFO 统计，五类应用消息真值；只为节点 4 出口 4 保存 `[2000,36000)` ns 详细事件 |
| `record-policy.json`、`export-policy.json` | 压缩记录和研究导出的空间、行数与索引上限 |
| `sample.json`、`replay.json` | 合并成 1270 ns 窗口；沿用图重新执行后端 |

`runs/native-run/observed/` 是研究者可读的阶段和端口统计；`truth/` 保存评价用来源；`raw/` 保留任务图和记录；`inputs/` 与 `logs/` 保存实际配置和资源报告。`slice inspect` 显示哪些端口、时间和类别被保存。各表通过清单指向压缩文件，读取方法见[输出说明](../../docs/record_storage.md)，不能把缺失范围补为零。

`sample-unsupported.json` 尝试将全范围改为 113 ns。这不与基础窗口对齐，选定的详细区间也不足以覆盖请求，因此以下命令应失败且不创建输出：

```bash
moe-ub sample --slice runs/native-slice --config examples/native_first_round/sample-unsupported.json \
  --output runs/native-unsupported
```

出口服务变化需要重新执行网络。另一个场景只将同一物理出口区间改为 0.5 Gbit/s；它不修改准备耗时或图内依赖。先解析，再重放：

```bash
moe-ub resolve --config examples/native_first_round/scenario-slower.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service --output runs/native-slower-input
moe-ub replay --slice runs/native-slice --config examples/native_first_round/replay-slower.json \
  --output runs/native-slower-replay
moe-ub validate --run runs/native-slower-replay
```

更换 `runs/native-input` 等默认目录时，也要修改 `run.json.inputs` 或 `replay-slower.json.network`。改变 STORE/LOAD、通信模板或已编入图的计算/准备成本，需要修改输入后重新 `run`。原生传输不使用 TP，多 TP 不属于本例；计算与通信参数都是参考值，结果不代表目标硬件时延。

需要核查原生请求为什么没有发出时，在上述 `resolve` 后运行：

```bash
moe-ub run --config examples/native_first_round/run-queue.json --output runs/native-queue
moe-ub validate --run runs/native-queue
```

`observation-queue.json` 选择 rank 2 从零时刻到 100000 ns 的请求队列，最多 100000 条记录，每条队列与在途请求合计最多 1024 项。输出 `truth/native_queue.jsonl` 是压缩存储中的逻辑表名，用 `stored_rows(Path('runs/native-queue/truth'), 'native_queue.jsonl')` 读取；`truth/native_queue_capture.json` 给出实际覆盖和完整性。字段见[请求队列格式](../../docs/record_storage.md#原生请求队列诊断)。这些内部身份只供机制核查，不属于算法可读的 `observed/`。启用记录不改变调度；超过任一记录上限会失败，不丢弃记录后宣称成功。
