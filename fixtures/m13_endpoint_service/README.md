# 端侧准备成本与物理出口服务区间

输入是三个 rank、一轮 Decode、一个 dense 层和三个 MoE 层。模型与路径沿用 [M12 合成输入](../m12_native_routing/README.md)，Dispatch 使用 STORE，Combine 使用有真实供数通知的 LOAD，CBFC 启用。未使用真实硬件校准成本。

`layers.json` 的额外成本是 rank 2 每个本地 token 的 Dispatch 准备 500 ns，以及 rank 12 每份专家输入的 Combine 打包 700 ns（第 2 层 1400 ns）；其他 rank 为零。已有成本保持。`scenario.yaml` 使用 `scenario-run/3`，对节点 4 的物理出口 4 在 `[0,100000)` ns 采用 1 Gbit/s，恢复后为基础 400 Gbit/s；对端不随之修改。执行预算为 1,000,000 ns。

先从新目录解析输入，再使用本目录 `run.json` 中已声明的输入位置执行。换用其他输出名时同步修改 `inputs`。观测使用本目录的 `observation.json`，记录及导出策略引用 `examples/first_round/` 的对应文件。所有配置路径相对配置文件解析。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario resolve \
  --input fixtures/m13_endpoint_service/scenario.yaml \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-port-service-input
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim run \
  --config fixtures/m13_endpoint_service/run.json --output runs/my-port-service-run
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim validate \
  --run runs/my-port-service-run
```

可复核的组合检查使用以下入口；它在输出中生成公开命令配置，逐条保存工作目录、参数、退出码和日志，并拒绝复用已有输出目录：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_endpoint_service.py \
  --build-dir build/v04-platform --output-dir runs/my-port-service-check
```

检查覆盖原成本、额外准备及限速三组实际联合执行；逐节点核对成本、依赖和有效交付；物理发包恰在区间起点、包跨越终点、恢复速率及反向端口不变；公开 `run`、`slice create`、固定图重放、观测不足拒绝、扩大窗口重新采集，以及执行预算超时与非法覆盖。速率边界独立检查使用固定选路；完整模型组合使用逐包喷洒，避免把喷洒下正常发包交错变化误判成速率错误。

执行结果、版本、资源和保留失败原因见 [result.json](result.json)。完整日志位于该摘要登记的 `runs/v04-platform/` 目录，未提交大型产物。测试通过不表示 CM384/R1 全规模或目标硬件校准已完成。
