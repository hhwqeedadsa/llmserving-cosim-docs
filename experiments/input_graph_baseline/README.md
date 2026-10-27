# 从真实输入生成的普通任务图强基线

本研究补强了现有图对照，没有提出新图算法。六个真实路由单层条件中，普通传递约简/共享join减少依赖边约23%～37%，全部操作与动态约束保持；不代表网络或仿真加速。方法和预算见[事前协议](protocol.md)，结果见[报告](results/20261027/report.md)、[网页](results/20261027/report.html)及[数据](results/20261027/summary.json)。

输入为八个用户提供的R1-AWQ/MMLU完整文件，batch8/16、EP4/8/16、E256/Top8/H7168，声明BF16。全部通过公开输入编译器及静态依赖接口生成，不依赖既有网络绑定。它仍是direct子集：没有执行slot分配、Combine动态绑定、完整Hybrid、网络包或GPU。

`analysis/study.py`生成并比较三种普通表示，`verify.py`独立核对全部可达关系和原始token选择，`report.py`生成中文解释并可重算约简，`recover.py`保存逐成员hash归档并从复制源码/空cwd离线复核。研究不导入平台测试或fixture驱动。

六条件执行额度已关闭，不能通过旧准备/执行命令追加条件。原件在`runs/input_graph_baseline_20261027/`，可仅离线重算已有图，输出必须新建：

```bash
PYTHONPATH=python:. python3 -B -m experiments.input_graph_baseline.analysis.report recompute \
  --stage runs/input_graph_baseline_20261027 --output /path/to/new-recomputed.json
PYTHONPATH=python:. python3 -B -m experiments.input_graph_baseline.analysis.verify \
  --stage runs/input_graph_baseline_20261027 --output /path/to/new-verification.json
```

核验会从已有配置重新调用Python输入编译器，不构建原生代码、发包或执行GPU。实际命令、资源、hash及复制恢复见[恢复](results/20261027/recovery.json)与[关闭](results/20261027/closeout.json)。完整数据包不随Git分发，原件不存在时不能只靠摘要重建真实路由。

现有结果不支持以“压缩静态依赖表”为主创新。与竞品比较时，应将整数表示、通用图约简、符号buffer及惰性展开作为可用强基线；本轮没有竞品速度排名或两项已成立的论文贡献。
