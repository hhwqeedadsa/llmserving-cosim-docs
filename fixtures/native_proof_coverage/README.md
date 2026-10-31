# 现代控制器紧凑因果覆盖验收

本增量用两份位集合保存全部已声明祖先的applied/completed等级，并在成功插入后累计原生节点数量，替代逐次复制字典和扫描历史节点。编号、前驱、回调、源缓冲保护、公开命令与输出schema保持；所有执行预算保持。要求见[规范](../../docs/tasks/native_proof_coverage_spec.md)，实际命令、结果和资源见[verification.json](verification.json)，可视化见[网页](report.html)。

新增7项方法、既有原生/批次/容量27项、计划/事件/窗口/批次重放/存储37项，共71项通过。独立带等级图遍历穷举729种四节点DAG、59,049种完整前驱组合及23,328次两级查询；另检查不升级applied、重复等级、自身/未来/未知id、空前驱、65535边界和计数/拒绝原子性。两个完整人工回调控制器与旧提交e55ae45eb1的计划、初始内存、绑定、全部原始回调、各事件前驱及提交/快照逐字节相同；这些回调是功能声明，不是网络观测。

相同4096节点人工依赖链，旧字典和新索引各在独立进程运行一次；24,570项查询和逐步前驱hash一致。以下是本次宿主观察，不是现代MoE完整任务或网络速度：

| 指标 | 旧字典 | 精确位集合 |
|---|---:|---:|
| 索引保留对象存储估计/B | 336,203,940 | 2,711,136 |
| 单进程峰值RSS/B | 359,956,480 | 21,258,240 |
| 构造与查询CPU/秒 | 1.9403 | 0.0536 |
| 构造与查询wall/秒 | 1.9417 | 0.0590 |

对象估计只统计索引保留对象，不含控制器、临时集合和进程其他内存；RSS还包含解释器与测量程序。各模式仅一次，无方差/稳健速度结论，不以性能阈值作为正确性断言，不能外推EP32实际任务的内存或推理时延。没有运行网络、GPU、构建安装下载或变更依赖。EP32完整连续调用的其他容量限制仍在，下一步需同步扩展运行入口和后端。

公开容量CLI从空cwd的正例、重复、容量拒绝、覆盖拒绝与非法输入流程通过。初始五阶段完成后，补充第六项显式原始回调/前驱对比；六个受监控阶段均完成，总资源预算不变，无失败或网络重跑。原件在runs/native_proof_coverage_20261031；预算600累计秒/256MiB产物/留8GiB，每阶段120秒/RSS1GiB/地址2GiB。仅发布user既有分支，整体研究目标active。

复现（零网络，宿主比较需足够内存）：

```bash
PYTHONPATH=python:tests/unit python3 -B -m unittest native_proof_coverage_test modern_native_test native_callback_batches_test modern_native_capacity_test
PYTHONPATH=python python3 -B tests/integration/check_modern_native_capacity_compat.py --baseline-commit e55ae45eb1 --output runs/my-proof-compat
PYTHONPATH=python python3 -B tests/integration/check_native_proof_coverage_host.py --nodes 4096 --output runs/my-proof-host
```
