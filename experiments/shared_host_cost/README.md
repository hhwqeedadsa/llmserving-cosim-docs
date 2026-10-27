# 共享上联场景的宿主成本

用途：判断包级优化可能消除多少模拟器CPU成本。它不重新比较网络配置，不测GPU推理性能；场景与预算见[事前协议](protocol.md)。

四次新增计时观测保持旧网络的全部结果与应用执行一致。当前原生CPU占已观测总CPU约80%—86%，但包含大量会话推进、排空检查、候选校验；Run exclusive约9%—10%仍不等于纯包处理。

**发现：标记Release的既有构建中，437个编译单元均被告警分支追加的`-O0`覆盖`-O3`。这些成本不能当优化后强基线。** 本阶段只读核查，没有构建优化版本或测量其收益。下一步应先独立修复构建，不能把常规编译优化当算法贡献。

结果入口：[说明网页](results/20261027/report.html)、[完整报告](results/20261027/report.md)、[CPU与编译摘要](results/20261027/summary.json)、[独立核验](results/20261027/verification.json)、[离线恢复](results/20261027/recovery.json)、[阶段收尾](results/20261027/closeout.json)。

## 离线复核

以仓库根目录为工作目录，`PYTHONPATH=python:.`；不安装新依赖。原件目录`runs/shared_host_cost_20261027/`，其中`checkpoint.tar.gz`与旁边的receipt记录固定hash及每个成员身份。

```bash
PYTHONPATH=python:. python3 -B -m experiments.shared_host_cost.checkpoint restore \
  --archive runs/shared_host_cost_20261027/checkpoint.tar.gz \
  --output runs/shared_host_cost_recovery_new
```

恢复目录必须不存在。恢复只用包中复制的源码，在空工作目录重算CPU和独立核验；不需要历史加载库路径，不执行native程序、网络、构建或GPU。原始实跑的动态库逐hash检查仍保留，离线路径投影单独说明。

`compiler.py`只读取既有编译数据库、缓存、CMake源码及构建provenance，以实际二进制/库hash关联运行时；`verify.py`用另一种逐记录时钟扫描方法核对CPU分解，并检查嵌套、CPU时间、物理包和应用完成四类篡改。四次观测额度已经用尽，`run.py`仅记录本次已执行流程，不作为追加重复的授权。
