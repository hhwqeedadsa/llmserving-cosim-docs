# A3 有限 MoE 层段

本例从一个包含 4 个块的小模型中选择原始第 2、3 层。三个 rank、6 个专家、Top-2 和请求路由均为合成输入，无需真实数据、模型权重或历史运行产物。通信采用 STORE/STORE、独立通信核、有限局部缓冲和原生请求池；容量仍包含完整模型。

完成[安装和构建](../../docs/user_guide.md)后，在仓库根目录执行。配置内路径相对配置文件；每个输出目录须尚不存在。

```bash
moe-ub resolve --config examples/a3_segment/scenario.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service --output runs/a3-resolved
moe-ub segment prepare --config examples/a3_segment/segment.json --output runs/a3-input
moe-ub run --config runs/a3-input/run.json --output runs/a3-run
moe-ub validate --run runs/a3-run
moe-ub facts --run runs/a3-run --config examples/a3_segment/facts.json --output runs/a3-facts
moe-ub segment freeze --from-run runs/a3-run --output runs/a3-slice
moe-ub slice inspect --slice runs/a3-slice --verify
moe-ub sample --slice runs/a3-slice --config examples/a3_segment/sample.json --output runs/a3-sample
moe-ub replay --slice runs/a3-slice --config examples/a3_segment/replay.json --output runs/a3-replay
moe-ub validate --run runs/a3-replay
```

自选构建目录时修改 `source-run.json.backend` 与 `replay.json.backend`，或在 `run`、`replay` 时使用 `--backend`。更换 `runs/a3-resolved` 时同步修改 `source-run.json.inputs`。

按通信核轮转 STORE 请求的参考配置使用同一合成模型，固定一个 worker、32 个总在途 STORE、256 个共享编号和 4096 B 请求粒度。它不代表已校准的硬件队列。运行入口为：

```bash
moe-ub resolve --config examples/a3_segment/scenario-round-robin.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service --output runs/a3-queue-resolved
moe-ub segment prepare --config examples/a3_segment/segment-round-robin.json --output runs/a3-queue-input
moe-ub run --config runs/a3-queue-input/run.json --output runs/a3-queue-run
moe-ub validate --run runs/a3-queue-run
moe-ub segment freeze --from-run runs/a3-queue-run --output runs/a3-queue-slice
moe-ub replay --slice runs/a3-queue-slice --config examples/a3_segment/replay.json --output runs/a3-queue-replay
moe-ub validate --run runs/a3-queue-replay
```

`queue-observation.json` 限定队列快照和全部 STORE 请求范围的记录量，两者只进入 `truth/`；字段与读取接口见[请求队列记录](../../docs/record_storage.md#原生请求队列诊断)。对照 FIFO 时，把场景属性 `ns3::UbLdstThread::StoreQueuePolicy` 改为 `fifo`，保持其余参数相同，使用新输出目录。旧 `scenario.yaml` 保留原来的三个 worker 设置，不能直接把它当作固定资源的 FIFO 对照。

| 输入 | 用途 |
|---|---|
| `scenario.yaml`、`backend-reference.yaml`、`fabric.json` | 两个平面的网络、逐包喷洒、CBFC、3 个原生 worker、32 个每 worker STORE 并发和 256 个每节点请求编号；不使用全 rank SEND 配额 |
| `service.json`、`layers.json`、`system.json` | 完整模型、路由、两个通信核和参考计算成本；不是目标硬件标定 |
| `source-run.json`、`segment.json` | 公共完整模型输入的位置及层段选择；第 0 个 Decode 步骤、原层号从 2 开始、连续 2 层 |
| `observation.json` | 操作阶段与端口聚合；默认不保存逐包明细 |
| `record-policy.json`、`export-policy.json` | 后端压缩记录与导出上限 |
| `facts.json` | 明确选择 `g0`、rank 2/7/12、第 2/3 层；限制节点、传输、输入计划大小和输出字节 |
| `sample.json`、`replay.json` | 合并窗口；重新执行保存的依赖图 |

`segment prepare` 只准备并检查输入，不运行仿真。输出 `service.json` 声明空网络从 0 ns 开始、首块输入已就绪、所选 Decode 步骤的 KV 已就绪，以及完整部署容量；保留原始请求和层编号。所有请求必须在同一初始批次中，不支持任意中途快照。超出上下文、缺少路由、选择非 MoE 层或无法容纳初始批次都会报错。

`runs/a3-run/observed/` 保存可供分析的操作和端口统计；`raw/` 保存执行核验记录。`runs/a3-facts/truth/` 是内部节点与交付事实，读取方法见[输出数据](../../docs/record_storage.md)。它不属于算法可读数据。`facts.json` 记录选定和实际覆盖，超限时保留日志并失败，不静默截断。

`segment freeze` 使用 `research-slice/3` 保存明确的有限范围。原有 `slice create` 会拒绝这种输入。移动保存目录后仍可执行 `sample` 和 `replay`；回放只运行后端，不重做前端排程或构图。修改层数、路由、准备成本等图内条件必须重新准备和运行。层段完成不表示生成了一个完整模型 Token，也不表示网络已达到稳态。

需要节省反复重放时的历史记录空间，可执行 `moe-ub slice compact --slice runs/a3-queue-slice --output runs/a3-queue-compact --storage hardlink`，再将 `replay --slice` 指向新目录。命令完整核验压缩记录和未改动图，保留原切片；不要修改共享的图或输入。压缩格式、复制选项及采样展开空间见[用户指南](../../docs/user_guide.md)。
