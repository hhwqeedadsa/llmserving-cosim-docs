# 真实请求扩充后的精简通信表示研究

本实验使用用户新增的八份完整DeepSeek-R1/MMLU路由，比较batch4/8、三种网络参数和三种精简通信表示；以完整现代功能调用及冻结通用事件图为详细参考。研究定义和资源上限见[事前协议](protocol.md)，结果见[报告](results/20261027/report.md)。这是单层EP4的功能网络研究，未执行GPU或完整请求服务，尚无新的论文主贡献证明。

## 首次执行

在仓库根目录使用下面的公开实验入口。两个输出目录须不存在，不能覆盖旧账目重跑。准备程序按接收清单核验八原件的本地hash，从既有检查点核验恢复原生运行时；不会联网或构建。若缺文件/归档则明确失败，不能用复制请求或其他版本替代。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.compact_network_choice.run \
  --repository /home/lry/moe-ub-cosim \
  --stage /dev/shm/moe-compact-choice-20261027 \
  --record /home/lry/moe-ub-cosim/runs/compact_choice_20261027
```

已保存的这轮12次网络都完成，最后一个严格比较因编号归一后after列表顺序改变而失败，执行账目关闭。不要重新运行上面命令覆盖事实；以下操作只读原件，生成新分析文件。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.compact_network_choice.diagnose \
  --stage /dev/shm/moe-compact-choice-20261027 --repository /home/lry/moe-ub-cosim
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.compact_network_choice.verify \
  --stage /dev/shm/moe-compact-choice-20261027
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.compact_network_choice.evaluate report \
  --stage /dev/shm/moe-compact-choice-20261027 --output /dev/shm/moe-compact-choice-20261027/final-report
```

实际执行须使用host_probe，保留该阶段1200秒/1GiB及每命令60秒/RSS1GiB/地址2GiB限额；精确命令、退出码、监控日志在持久目录。`verify`使用新observed-only-predictions目录，不可覆盖运行，重复离线复核应从归档恢复到新目录。

## 文件作用和边界

- `prepare.py`：核验八原件、固定输入和三网络配置，恢复已核hash的运行时。
- `compact.py`：只读observed，聚合七阶段端点需求/声明本地成本，产生三种不拟合预测和配置选择。phase模型保留计数/屏障、单向传播及MRT1帧服务，不建详细队列或QP反馈，不是严格上下界。
- `run.py`、`generic.py`：预算/停止账目、公开CLI执行，以及只从各batch原网络提取冻结通用事件图。
- `evaluate.py`：评价真实完成边界、稳定身份等价性、配置选择后悔值和时延误差。
- `diagnose.py`、`verify.py`：保留严格失败，核实AND前驱列表排列、独立枚举原始选择及消息量、证据篡改拒绝和移除truth后的预测一致性。
- `checkpoint.py`：按成员hash保存证据及源码，从新源码根/空cwd离线复现预测和报告。

新样本仅有用户确认目录、本地hash及历史来源路径；远端固定版本身份仍未独立核实。声明BF16激活不由AWQ权重精度推导。batch增加请求数并改变输入内容，不是卡数变化；网络升级对照不代表同成本收益或商业ROI。报告保留“选对配置但时延误差仍大”的区别。
