# 完整副本的收益与正常代价

本实验比较同一逻辑任务与部署下的无冗余SR和立即完整副本。先读[执行前方法](protocol.md)，再看[当前结果](results/20261030/README.md)和[网页](results/20261030/report.html)。这不是新算法或完整R1性能基准，成熟延迟备用仍需实现。

## 复现流程

先按[公开示例](../../examples/scaleout_whole_call/README.md)使用本机已取得并校验的来源清单，增加`--include-primary-control`，生成`service-whole.json/service-primary.json`。两策略使用同一128端点Clos拓扑和原尝试图，normal/local分别使用`rdma.json/rdma-whole-local.json`。只在登记预算后通过公开`scaleout rdma-run`执行，每个输出目录应为新目录；随后`scaleout rdma-check`。

本轮已有完整eager结果，因此只新增无冗余两个条件。执行前冻结：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/whole_replica_value/prepare.py \
  --prepared /path/to/prepared \
  --eager-normal /path/to/eager-normal --eager-local /path/to/eager-local \
  --backend /path/to/native-service --output /path/to/new-frozen.json
```

冻结器按本轮四份来源和固定后端身份校验；它不下载数据、不执行网络。其他来源/后端应另立有依据的方法，不改SHA绕过输入检查。无副本准备文件须由公开示例生成，不手工改成EP128或删除节点。

分析既有结果：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/whole_replica_value/analyze.py \
  --frozen /path/to/frozen.json --normal /path/to/primary-normal \
  --local /path/to/primary-local --output /path/to/new-analysis
```

此命令只读四份结果及原件，不执行或重放网络。它核对固定方法、原件SHA、整个原尝试图、拓扑/协议、实际原生OBSERVE、源发送与设备TX、连接有效字节/接收前缀、全部阶段门限和完整结果，分别输出`analysis.json/report.html`。逻辑输入相同，冗余物理工作量有意不同。输出目录必须不存在。

原件未随仓库发布。取得相同来源清单与原始文件后可重新准备；固定机器的完整命令、输出SHA和宿主资源见结果收据。GPU成本仍为声明值，不能把可复现等同于硬件校准。
