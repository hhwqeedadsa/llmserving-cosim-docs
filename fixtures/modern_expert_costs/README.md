# 专家阶段成本矩阵的功能验收

公开用法见[现代入口](../../examples/modern_deepep_native/README.md#按调用和rank修改专家计算耗时)，范围与预算见[平台规范](../../docs/tasks/modern_expert_costs_spec.md)。本目录检查输入与实际完成因果，不评价研究收益、生产热点概率或GPU性能。

v1使用单个`expert_ns`；v2按调用/rank显式给出矩阵，其他本地准备/归约成本仍独立声明。专家工作在真实Dispatch完成后进入同一原生网络时钟的每GPU串行lane，Combine依赖真实完成回调。矩阵只给相对耗时，不给固定通信发起时刻，也不根据未来网络结果拟合。

小例是两rank、两调用、H256/Top2/E4的合成字节功能输入，使用已有选择性重传基线，正常条件不注入故障。GPU类型为NONE，成本均是声明值，不是DeepSeek-R1全模型、硬件profile或训练样本。

验收固定4个网络条件：v1固定1000ns；v2等值矩阵；v2的`[[0,5000],[7000,100]]`异值矩阵；新源码根、空cwd和复制运行时的异值迁移。前三个由`accept.py`经公开CLI运行，每次先完成独立核验再推进。第四个由下面的恢复命令执行；任一失败关闭剩余额度，不自动提高上限或重跑。

`verify.py`逐专家节点核对Dispatch前驱、实际admitted/started/completed、声明duration及Combine后继；并复用独立应用/内存/MRT1源负载/实际callback批次核验器。`completed-started`核对服务时间，排队不算入计算成本。四类篡改分别改变duration、实际完成时刻、Dispatch依赖和来源说明，都必须拒绝。等值条件比较完整result/control/现代快照，只有v2新增矩阵说明单列；迁移只允许运行库根路径变化，保留列举顺序与所有执行字段。

接收检查、正常功能和性能结论分开：即使所有验收通过，也只表示可以显式改变计算就绪条件并正确等待，不能据此声称热点计算已校准或两个创新候选已经成立。完整请求、较大EP和强基线误判仍需独立研究。

本机原件为`runs/modern_expert_costs_20261027/`，包括输入、完整结果、实际命令、监控、运行时身份、原v1源码回放、篡改拒绝与失败记录。版本化摘要入口为[功能核验](verification.json)、[归档](checkpoint.json)、[迁移复现](recovery.json)和[收尾](closeout.json)。

独立恢复需要现有Python环境，不安装、下载、构建或运行GPU。**下面命令会先离线复核三份历史结果，再执行一次小型网络迁移**；不能称作纯离线重算。输出目录必须不存在：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/my-expert-costs-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 268435456 --wall-seconds 60 \
  --artifact-root runs/my-expert-costs-restored --min-free-bytes 8589934592 -- \
  python3 -B -m fixtures.modern_expert_costs.checkpoint restore \
  --archive runs/modern_expert_costs_20261027/checkpoint-source-bound.tar.gz \
  --output runs/my-expert-costs-restored
```

若只需核对已有异值结果，在相同host_probe预算内调用，不创建网络：

```bash
PYTHONPATH=python:. python3 -B -m fixtures.modern_expert_costs.verify \
  --run runs/modern_expert_costs_20261027/skew \
  --output runs/my-expert-costs-verification.json --corruptions
```

归档保留成员hash、权限、复制源码、固定session与8库；恢复拒绝路径穿越、链接、重复成员、文件父子冲突及超限。恢复前先验证归档摘要和每个成员，再在复制源码及空工作目录中重新核验。迁移后核对实际加载库hash与完整网络/应用行为，不依赖旧构建目录或外部账号。
