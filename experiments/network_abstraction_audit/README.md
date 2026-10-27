# 专家热点、通信表示与网络仿真近邻

本阶段只读原始结果并做离线判别，不实现新的网络模型、不运行GPU或扩大实验条件。[范围与预算](protocol.md)、[报告](results/20261027/report.md)、[独立网页](results/20261027/report.html)、[来源](results/20261027/sources.json)分别保存方法、结果和证据边界。

新增13份文件的目录来源已由用户确认；原始接收结果是11份完整、2份末尾缺层，连同旧4份共15份完整文件。本阶段使用已用于对照的其中8份，未把来源目录确认写成远端固定修订验证。

核心发现：固定每个token在各Top-K位置的目的rank，改变卡内专家集中程度，可以让batch8最热专家的token数从1变为8；排除专家计数块的内容后，生成的通信形状和依赖不变。当前每rank专家计算为固定1000ns。该发现限定于本轮编译器、EP4与人工变换类别，不是新包级实验或真实GPU等价结论。

本地原始记录保存在 `runs/network_abstraction_audit_20261027/`。恢复不需要旧网络二进制、外部账号、联网或重新仿真，只需Python 3和已有的`pdftotext`。归档含用户提供的路由原件，只保留在本地忽略目录，不上传或提交Git。

从仓库根目录恢复到一个不存在的新目录：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m moe_ub_cosim.host_probe \
  --output-dir runs/my-abstraction-audit-restore-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 134217728 --wall-seconds 60 \
  --artifact-root runs/my-abstraction-audit-restored \
  --min-free-bytes 8589934592 -- \
  python3 -B -m experiments.network_abstraction_audit.checkpoint restore \
  --archive runs/network_abstraction_audit_20261027/checkpoint.tar.gz \
  --output runs/my-abstraction-audit-restored
```

恢复逐成员核hash，从复制源码和空工作目录重算六份计划、汇总、独立核验、来源索引及报告，要求逐字节一致。`source/`中含本阶段与所需平台Python源码；原有功能模型和依赖保持。脚本不会自动重新获取已经失败的HTTP或重跑网络。

若直接检查已有原件，使用两个离线入口，输出都必须是新路径：

```bash
PYTHONPATH=python:. python3 -B -m experiments.network_abstraction_audit.study analyze \
  --run runs/network_abstraction_audit_20261027 \
  --output runs/my-abstraction-analysis

PYTHONPATH=python:. python3 -B -m experiments.network_abstraction_audit.verify \
  --run runs/network_abstraction_audit_20261027 \
  --analysis runs/my-abstraction-analysis \
  --output runs/my-abstraction-verification.json
```

执行时仍按协议放入`moe_ub_cosim.host_probe`预算监控；两个入口只读取已备份的八份原件与旧观察输入，不读取网络未来结果。来源抓取入口仅用于本阶段已关闭的八次HTTP，不是离线复核的必要步骤，不应自动再次运行。
