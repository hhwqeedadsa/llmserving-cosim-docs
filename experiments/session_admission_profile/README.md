# 完整候选准入的重复处理

为了判断“更快的MoE网络仿真”应先改哪里，用四份O3实际完成调用的前缀离线剖析公开完整校验。范围与停止规则见[事前协议](protocol.md)。本轮没有新网络、GPU、构建或平台改动。

结果：[网页](results/20261027/report.html)、[详细解释](results/20261027/report.md)、[数据](results/20261027/summary.json)、[独立核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)、[收尾](results/20261027/closeout.json)。原件在`runs/session_admission_profile_20261027/`。

167/207个最终节点，在67/92次实际COMMIT中累计进入主操作校验循环6769/11719次，约40.53/56.61倍。固定streams子校验占新剖析时间33.57%～35.68%，达到事前10%工程优先级门槛，可以安排类型安全的固定校验复用对照。旧完整记录中，全部Python/C++候选复制/校验占CPU35.22%～41.47%；将它们全部免费也只有1.544～1.708倍的宽松CPU工作量界，不是已实现速度或墙钟界。

cProfile有额外开销，其比例不能乘旧CPU推算加速。剖析一次/条件，不提供稳定速度排名。缓存和增量检查属于强基线改进；本轮没有实现缓存或确立论文创新。

公开入口只使用`scaleout_transactions.validate()`与`host_timing.read_trace()`，不调用测试、fixture或原资源测量驱动。`study.py`保存真实前缀hash、剖析和复制计时；`verify.py`不调用平台校验，用原始COMMIT独立枚举前缀与远端历史对，重新核原CPU根区间并拒绝四类篡改。

新剖析须重新确认范围与预算，不能用新目录绕过失败或重跑本阶段。以下运行命令自己逐项调用host_probe，输出目录必须不存在：

```bash
PYTHONPATH=python:. python3 -B -m experiments.session_admission_profile.study run \
  --repository "$PWD" --run "$PWD/runs/session_admission_profile_new"
```

成员hash归档支持复制源码/空cwd离线恢复，仅重算分析、核验和网页，**不重测profile或运行网络**；外层仍须保持协议的监控和累计预算，目标目录必须不存在：

```bash
PYTHONPATH=python:. python3 -B -m experiments.session_admission_profile.checkpoint restore \
  --archive runs/session_admission_profile_20261027/checkpoint.tar.gz \
  --output runs/session_admission_profile_recovery_new
```
