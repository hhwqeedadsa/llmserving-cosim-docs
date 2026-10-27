# 共享上联、多物理网卡与网络配置选择

研究对象是给定真实专家选择后，哪些网络升级能减少单层 MoE 通信等待，及普通服务量模型是否已经足够决策。输入、假设和预算见[事前协议](protocol.md)。不改平台或依赖，不把经典阶段/链路负载估算当新算法。

固定平台 `5e7f792` 的物理 NIC 绑定能力，使用8份完整MMLU便利样本、四活动rank、batch4/8；两张NIC同plane、两rail共享Agg/Core。baseline接入100G/核心25G；access接入200G/核心25G；core接入100G/核心50G。不是完整模型服务或千卡实验，MRT1/provider/计算均为声明功能模型，未校准硬件性能。

## 结果入口

[网页报告](results/20261027/explanation/report.html)、[完成时间和选择表](results/20261027/report/report.md)、[机制解释与研究判别](results/20261027/explanation/conclusions.md)、[独立核验](results/20261027/verification.json)。原始文件与固定运行时保存在 `runs/shared_uplink_choice_20261027_r2/`，零网络准备失败保存在 `runs/shared_uplink_choice_20261027/`。不要覆盖或重复这些网络。

接入升级的应用等待改善0.56%/0.48%，核心升级改善19.14%/27.28%。只计端点或每NIC会错选接入，逐链路阶段模型已选对；6组完整通用事件图保持行为等价。两项创新尚未成立，本轮没有新的推理或仿真加速方法。

## 从新目录执行

必须先具备接收清单中的八原件和协议指定的物理绑定检查点；不会下载或构建。输出目录必须不存在。下例会执行12次新网络，须使用协议要求的累计1200秒/1GiB及每命令60秒限制；研究执行器内逐步监控，任何失败立即关闭剩余网络，不自动加限。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.shared_uplink_choice.run \
  --repository /home/lry/moe-ub-cosim --output /home/lry/moe-ub-cosim/runs/shared-uplink-new
```

首次真实执行只有准备错误、没有网络：误比较了同组不同token的slot变量名。修正保留组的实际count/prefix参数；新目录复用已有输入和运行时，batch4预测逐字节保持；两个目录合计预算不变。`--reuse-failed-preparation`只服务这个已登记的零网络错误，不用于重开网络失败。审计初版将61层误当只有58层、随后误把前三层null写成空数组的两次检查失败均保留，没有重跑网络。

## 离线核验与解释

以下命令不执行网络。第一次核验使用新的`observed-only-check`目录；后续重复宜从检查点恢复到新目录，不覆盖旧文件。仍须用`moe_ub_cosim.host_probe`按协议监控并将所有新产物计入累计预算。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.shared_uplink_choice.verify --stage RUN/stage
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.shared_uplink_choice.evaluate report --stage RUN/stage --output RUN/stage/report
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.shared_uplink_choice.explain --stage RUN/stage --output RUN/stage/explanation
```

`predict.py`只接收observed输入，按slot双射的多重集合计算精确的本输入累计NIC帧负载；不预设动态token排序。`generic.py`通过公开会话执行从各batch原网络冻结的完整图；`evaluate.py`只归一临时编号和AND前驱排列，保留提交与应用事件顺序。`audit.py`独立枚举物理路径、核对全部TX/RX/串行传播/端口字节/队列积分及实际绑定帧负载；FIFO仅核查捕获协议各自的顺序，不推断跨优先级顺序。`explain.py`单列累计包等待、CNP谱系与逐跳发送、应用与排空尾部。

按成员hash恢复到新目录，再从复制源码/空cwd重新计算预测、核验、评价和网页，零构建/网络：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.shared_uplink_choice.checkpoint restore \
  --archive runs/shared_uplink_choice_20261027_r2/checkpoint-v2.tar.gz \
  --output runs/shared_uplink_choice_20261027_r2/new-restored
```

归档身份、恢复和累计资源见[收尾记录](results/20261027/closeout.json)。包含原件不意味着远端版本已核实；本地hash、用户确认目录、功能正确与硬件校准分别成立。
