# 相同核心上联总带宽的多路径对照

本研究检查：有限连接、不同路径数和拥塞反馈，是否使普通阶段模型选错网络配置。事前方案见[协议](protocol.md)，实际结果见[网页](results/20261027/report.html)、[报告](results/20261027/report.md)和[研究决定](decision.md)。

三种网络的每个汇聚交换机核心上联总带宽均为100G，分别使用1×100G、2×50G、4×25G。交换机与端口数量不同，不能称相同设备成本。真实路由来自DeepSeek-R1-AWQ/MMLU，实际执行为四卡、单层、一次Decode通信调用，声明计算成本，不是完整模型服务或GPU实测。

## 结果与边界

- batch4的现代调用与通用完整任务图共执行6次，全部底层网络完成。应用阶段依次58.568、63.668、75.879微秒；单核心相对四核心节省22.81%，这是拓扑敏感性，尚无新算法收益。
- 保留连接哈希的两种普通参考都选对单核心，选择后悔值为0；但最大绝对相对时延误差仍为14.23%和26.74%。等分参考三配置并列，依靠事前规则选择单核心，不能说它辨别出了优劣。
- 单/双核心的完整严格比较通过。四核心仅回调列表顺序失配；离线诊断定位到6个相同时间戳桶的20个位置，操作历史、批次边界、包、队列、应用时刻与内存相同。保留原失败，batch8六次网络不再执行，不将诊断改写为严格通过。
- 独立核验重算三组包级物理路径/时序/字节/队列、公开事务需求与完整比较，核对八个实际加载库，拒绝8类篡改。复制源码和空工作目录离线复算预测、诊断、核验与报告；恢复不运行网络。

输入接收记录包含17份JSON，15份完整，899和1479缺层而整份排除。用户确认新增13份都来自`cognitivecomputations/DeepSeek-R1-AWQ/mmlu/`。该来源确认不等于独立验证远端下载修订；不是随机或严格留出样本。

说明更正：事前协议将拓扑节点0/1/4/5写成rank；实际输入始终是逻辑rank0/1/2/3分别部署到GPU节点0/1/4/5。原协议保留，不更改已冻结的输入或实验条件。

## 代码与复核

| 文件 | 用途 |
|---|---|
| `data.py`、`predict.py` | 校验原件/运行时；网络执行前冻结三个普通预测器 |
| `generic.py`、`run.py` | 通过公开会话执行完整任务图；首次严格失败关闭后续网络 |
| `audit.py`、`verify.py` | 独立核对物理网络、来源和声明需求，保留失败并拒绝篡改 |
| `diagnose.py` | 只离线解释原回调顺序差异，不改变原门槛 |
| `evaluate.py` | 从保留结果生成JSON、Markdown和HTML |
| `checkpoint.py`、`finish.py` | 逐成员哈希归档、复制源码恢复及原预算内收尾 |

原件位于`runs/ecmp_network_choice_20261027/`。预算为1800受监控秒/1GiB累计产物，每命令60秒/RSS1GiB/地址2GiB，磁盘保留128MiB。本轮零构建、安装、下载、GPU或依赖修改，只有本地提交。

已关闭的网络研究不重跑`run.py`。已有包的离线恢复命令如下；新输出目录须不存在，SHA256从版本化的[归档收据](results/20261027/checkpoint.json)读取：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m moe_ub_cosim.host_probe \
  --output-dir runs/ecmp-choice-review-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 268435456 --artifact-root runs/ecmp-choice-review \
  --wall-seconds 60 --min-free-bytes 134217728 -- \
  python3 -B -m experiments.ecmp_network_choice.checkpoint restore \
  --archive runs/ecmp_network_choice_20261027/checkpoint.tar.gz \
  --sha256 "$(python3 -c 'import json; print(json.load(open("experiments/ecmp_network_choice/results/20261027/checkpoint.json"))["sha256"])')" \
  --output runs/ecmp-choice-review
```

这是已有证据的离线复核方式，不是从零构建，也不增加原研究的网络次数。完整记录见[核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)和[资源收尾](results/20261027/closeout.json)。
