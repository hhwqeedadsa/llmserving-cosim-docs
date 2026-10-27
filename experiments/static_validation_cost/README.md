# 固定校验复用的完整流程成本

本阶段问的是“重复静态检查减少后，整次仿真能省多少”，不是检查缓存是否正确。平台功能已在此前独立验收；本研究不修改平台、构建或依赖。事前范围见[协议](protocol.md)，取舍见[研究决定](decision.md)。

24次无细粒度日志的成对运行与4次单独诊断均完成，完整包、队列、控制与应用保持。四条件CPU倍率中位数为0.985、1.009、1.050、1.010，均未达事前工程阈值；墙钟为1.003、1.004、1.054、0.983。三对存在波动与回退，不声称显著或普遍加速。缓存保持默认关闭，普通校验复用不是论文贡献。

来源为八份真实R1-AWQ/MMLU路由、单层entry1/layer3、EP4、batch4/8、E256/Top8/H7168/BF16；四活动rank、同plane双物理NIC及共享Agg，接入100G/核心25G/每链路传播1us。计算和provider为声明成本，没有GPU执行、实机校准、完整模型请求或千卡外推。通用图保留旧实际绑定的完整操作/依赖，不读取参考时刻，也不是输入独立编译器。

CPU由Linux wait4读取完整命令及已等待子孙的user+system，墙钟取单个命令区间，RSS是10Hz采样的进程树和。现代公共CLI包含其内部监控，通用执行器通过公开session运行；每条件内部两模式策略相同，但不能用两类执行器绝对CPU差直接推断语义收益。详细日志只在后四次开启，解释剩余阶段，不混入速度统计。

所有原件、实际命令、wait4原数、主机负载与配对次序在`runs/static_validation_cost_20261027/`。`study.py`经公开命令执行现代工作；`generic.py`用既有EventGraph语义和公开session推进，不调用tests、fixtures或历史资源驱动。`verify.py`独立重核28个内核收据、28份完整网络/快照、12对与8份时钟扫描，并拒绝CPU算术、顺序、包、应用、缓存数五类篡改。

结果：[网页](results/20261027/report.html)、[说明](results/20261027/report.md)、[全部原数与指标](results/20261027/summary.json)、[独立核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)和[资源收尾](results/20261027/closeout.json)。

从逐成员hash归档离线重算报告与核验，使用复制源码及空cwd，不重复网络或性能测量。输出目录须不存在；在60秒/RSS1GiB/地址2GiB及本阶段总预算下执行：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.static_validation_cost.checkpoint restore \
  --archive runs/static_validation_cost_20261027/checkpoint.tar.gz \
  --digest 69a27112c02930f6aeab86a4d1146d3b28828da0d681771a3c43ebdf974beb50 \
  --output runs/static_validation_cost_recovery_new
```

原输入从固定归档`4d7360e1c18b6847ebeb0fa670aa705232c15bce2b9d0df0279ac4ea127473b9`取得；可按原协议重新开展一组独立研究，但本阶段28次额度关闭，不因收益未达阈值扩条件或补跑。复核产物、原始路由和运行时留`runs/`，原始路由不入Git。
