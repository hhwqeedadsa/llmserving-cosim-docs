# 专家就绪与网络配置选择

先回答一个可证伪的小问题：不同卡计算耗时不同时，知道最长耗时的普通逐链路参考会不会选错网络方案？范围、成本假设、判据和资源上限见[事前协议](protocol.md)。

沿用八个已确认完整MMLU原件与公开现代功能执行入口；不是GPU校准或现代通信库的生产性能复现。研究代码不依赖tests/fixture，不改平台与固定依赖。所有预测在新网络前冻结，完整图只从各条件baseline提取，不能称输入直接生成。

以下命令从仓库根目录启动；输出目录必须不存在。先核对已有原件与验收O3运行时、预算和协议，再执行，失败不得以新目录重试网络。

```bash
PYTHONPATH=python:. python3 -B -m experiments.readiness_network_choice.run \
  --repository "$PWD" --output "$PWD/runs/readiness_network_choice_new"
```

完成后用独立审计和报告入口（scratch/output必须不存在）：

```bash
PYTHONPATH=python:. python3 -B -m experiments.readiness_network_choice.verify \
  --stage runs/readiness_network_choice_new/stage \
  --scratch runs/readiness_network_choice_new/offline-check \
  --output runs/readiness_network_choice_new/stage/verification.json
PYTHONPATH=python:. python3 -B -m experiments.readiness_network_choice.report \
  --stage runs/readiness_network_choice_new/stage \
  --output runs/readiness_network_choice_new/stage/report
```

每条命令必须保持协议的host_probe监控与累计预算，不能只限制单次网络。保存原始来源、prediction/input identity、execution账目、完整捕获和失败；原始路由不提交Git。结果和归档恢复入口在本轮完成后链接，不把未运行命令当完成证据。
