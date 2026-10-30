# 在线检查点信号：先判断已有信息是否足够

本轮只读已有128端点Clos的正常/局部故障无备用控制，比较首个Dispatch、第一Decode步、完整两步三个正常校准检查点。研究的是更早判断备用资格是否需要复杂遥测，不执行备用，不报告假想网络收益。方法见[事前约定](protocol.md)。

准备器负责保存原始身份、从正常样本校准和划分严格过去的观测前缀；判断器只读计划与过去事件；评价器再读完整记录。判断器不导入平台消费者、测试或评价真值，不访问未来图、故障或包路径。报告须区分判断信号可用、平台策略已实现、真实网络收益三个层次。

[结果网页](results/20261030/report.html)与[结论](results/20261030/README.md)：阶段检查19µs，已有正常样本校准源确认超时参考19.030µs，只差30ns，不能将检测提前归给新复杂机制。源确认参考的补充规则见[方法](watchdog_protocol.md)；20µs来自既有示例，18µs仅依据正常跨度推导，都不是默认值或生产p99。

## 完整复核

在仓库根目录，使用现有前端环境和本地原件；不下载、安装、编译或执行新网络。各输出目录或文件必须不存在，先落实方法中的累计预算：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/checkpoint_signal/prepare.py \
  --previous-study runs/sequential_whole_20261030/study \
  --output runs/checkpoint_signal_20261030/study
PYTHONPATH=python .venv/frontend/bin/python -B experiments/checkpoint_signal/run.py \
  --prepared runs/checkpoint_signal_20261030/study \
  --output runs/checkpoint_signal_20261030/decisions
PYTHONPATH=python .venv/frontend/bin/python -B experiments/checkpoint_signal/evaluate.py \
  --prepared runs/checkpoint_signal_20261030/study \
  --decisions runs/checkpoint_signal_20261030/decisions \
  --output runs/checkpoint_signal_20261030/analysis
PYTHONPATH=python .venv/frontend/bin/python -B experiments/checkpoint_signal/network.py \
  --prepared runs/checkpoint_signal_20261030/study \
  --output runs/checkpoint_signal_20261030/analysis/network.json
PYTHONPATH=python .venv/frontend/bin/python -B experiments/checkpoint_signal/watchdog_reference.py \
  --prepared runs/checkpoint_signal_20261030/study \
  --output runs/checkpoint_signal_20261030/analysis/watchdog.json
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/checkpoint_signal/report.py \
  --prepared runs/checkpoint_signal_20261030/study \
  --analysis runs/checkpoint_signal_20261030/analysis \
  --output /path/to/new-report
```

完整核验需要原来两份真实网络结果、匹配平台、依赖及路由原件；准备器用公开`scaleout rdma-check`只读检查，不执行`rdma-run`。在另一机器上须先按[原实验入口](../sequential_externality/README.md)获得合法来源与匹配结果，不能改历史冻结文件来冒充相同执行。已有成功网络不重跑。

## 不依赖原路由或旧运行目录的CPU演示

仓库随报告保存3份初始检查点计划和6份最小过去视图，只演示已有完成标志如何用于判断，不替代完整网络复核。正常首个Dispatch示例：

```bash
.venv/frontend/bin/python -B experiments/checkpoint_signal/classify.py \
  --plan experiments/checkpoint_signal/results/20261030/plans/first_dispatch.json \
  --events experiments/checkpoint_signal/results/20261030/minimal/first_dispatch-normal.jsonl \
  --view local_member --output /path/to/new-normal-decision.json
```

正常返回`defer_needs_later_check`，表示本阶段已完成，仍需要后续检查，不释放备用KV。将事件文件换成`first_dispatch-local.jsonl`返回`consider_subject_to_budget`，仅表示可考虑备用，仍没有发包、恢复执行或收益。最小故障视图为空是原始完整过去前缀中未出现对应完成回调，不是空任务；来源完整性由独立评价和冻结哈希证明。
