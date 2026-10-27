# 优化后的共享网络成本基准

先修复Release被`-O0`覆盖的配置，再用已验收O3构建观察旧负载，判断值得优化的部分。范围和预算见[事前协议](protocol.md)。本阶段没有新构建或平台改动。

四次观测均保持旧完整网络/事务状态、control和应用执行；已知加载库位置与列举顺序按事前规则核对多重集合和运行时hash。优化后原生占已观测总CPU约53%—57%，Run exclusive约3.7%—4.2%，不能继续使用旧O0的80%—86%来承诺包级收益。任务校验、复制和会话推进仍占明显成本。

口径：八份真实MMLU路由，entry1/layer3；batch4/8是总token数4/8，每rank1/2个，EP4/E256/Top8/H7168/BF16，四活动rank、同plane双NIC共享Agg，接入100G/核心25G/链路传播1us。计算/provider为声明功能成本，非完整多层服务或生产千卡负载。每条件只观测一次，记录刷新开销包含在CPU中，不作稳定墙钟排名；常规编译修复不算新算法收益。

结果：[网页](results/20261027/report.html)、[说明](results/20261027/report.md)、[全部阶段数据](results/20261027/summary.json)、[独立核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)、[收尾](results/20261027/closeout.json)。

所有原件在`runs/optimized_host_cost_20261027/`。`study.py`只使用公开CLI、NativeTransactionSession入口和稳定计时读取；完整通用图执行器沿用已验收独立研究代码，不调用测试或fixture。`verify.py`独立扫描旧/新时钟记录，核对完整行为、CPU算术，并拒绝四类篡改。

从成员hash归档在复制源码/空cwd恢复摘要、报告和核验，不再执行网络或构建；目标目录必须不存在：

```bash
PYTHONPATH=python:. python3 -B -m experiments.optimized_host_cost.checkpoint restore \
  --archive runs/optimized_host_cost_20261027/checkpoint.tar.gz \
  --output runs/optimized_host_cost_recovery_new
```

宽松“全部native免费”CPU工作量倍数仍将原生初始化/校验/报告都当可消除，保持Python不变；不能当墙钟、GPU、千卡或纯包加速上限。Run exclusive含混合处理，包引发的通知还可能落在其他阶段，不能从该比例给所有包相关算法设严格上限。两个论文候选仍须实际方法、强基线、校准与泛化证据。
