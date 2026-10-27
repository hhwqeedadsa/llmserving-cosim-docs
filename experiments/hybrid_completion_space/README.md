# 分层MoE结束通知的剩余收益空间

先看[中文网页](results/20261027/report.html)、[文字报告](results/20261027/report.md)及[36组数据](results/20261027/key_metrics.json)，范围和停止规则见[事前协议](protocol.md)。这是固定DeepEP源码及真实路由约束的API需求筛选，未执行网络、GPU或新协议。

15份完整R1-AWQ/MMLU文件，prefill每层693token、首次decode15token，58层，EP64/128/256、每服务器8卡、C4/8/16及两源部署。普通因子化参考知道活跃通道与对端，精确参考再保留它们的关联。数据/控制、Dispatch/Combine各自核对，不能把一侧节省乘2。

decode额外调用节省为0。prefill的C16条件还有10.4%～15.0%控制API差额，但C4普通参考调用数比C16精确参考更少5.9%～27.8%；并行性能、网络时延与协议正确性未验证。因此没有两项已成立的创新，也不把普通活动位图写成新方法。

这里的控制API计数专指跨服务器的signal、put与red_add_rel发请求调用，不含本地flush调用、self和NVLink操作。flush/ACK服务成本仍未建模，不能据计数将其删除或视为免费。

`analysis/study.py`准备/核算，`verify.py`用位图独立核两方向/计数/来源，`report.py`解释与比较通道数，`recover.py`按成员hash归档并从复制源码/空cwd复算。它们不导入平台tests/fixtures或历史资源驱动。小R4/C4只调用公开Hybrid编译/绑定，不运行网络；大EP/长prefill核算不表示平台已经支持这些完整执行。

原件位于`runs/hybrid_completion_space_20261027/`。本轮分析额度已关闭，不能用旧准备/执行命令追加输入；可以只核对已有原件，输出须使用新路径：

```bash
PYTHONPATH=python:. python3 -B -m experiments.hybrid_completion_space.analysis.verify \
  --stage runs/hybrid_completion_space_20261027 --output /path/to/new-verification.json
```

该命令不会联网、构建或发包。来源仅有用户确认目录、本地接收hash和固定缓存blob，不冒充新增远端版本验证。首次核验器的Dispatch入口布尔文字锚点错误及失败日志保留，修正后只做离线核验，没有重跑计数条件。

逐成员hash恢复与资源收尾见[恢复](results/20261027/recovery.json)和[关闭](results/20261027/closeout.json)。完整原始文件/归档不随Git分发；缺原件时摘要不能代替复算输入。后续必须比较同信息/资源下的通道调参与完整目标协议，API差额不是丢包恢复、网络时延或宿主加速收益。
