# 本轮结果与复核入口

[中文网页](results/20261030/report.html) · [详细解释](results/20261030/README.md) · [逐组结果图](results/20261030/comparison.png) · [集合通信到达曲线](results/20261030/arrival_curves.png) · [原始核验](results/20261030/raw_verification.json) · [展示及篡改检查](results/20261030/verification-v2.json) · [身份与资源](results/20261030/receipt.json)。

七个128Clos真实选择驱动的网络各执行一次。20µs短时上行故障下，本地过滤使最晚任务181.405→147.706µs，减少18.58%、收回故障额外等待73.85%；60µs故障下只从248.362→244.806µs，减少1.43%、收回3.16%。相同SR参数下，重传减少并不意味着等比例提速。

发现的主要长等待来自确认返回路径：数据已到，但聚合交换机过滤坏端口后没有其他原有等价候选，ACK被丢弃；链路上线不自动重发已丢弃ACK，发送端仍等下一轮超时。已有即时全局改路参考显著降低此等待，因此下一步先补完整的现成局部绕路基线，再判断MoE进度感知恢复的独立贡献。没有实现或宣称新协议收益，normal及instant差距不视为可消除上限。

## 复核已有原件

本机根为`runs/transient_uplink_recovery_20261030`。`study/frozen.json`保留初版；`frozen-v2.json`保留独立构建核查修订、已知normal原件及不变输入。新故障运行前才冻结v2；执行源码位于提交`feeeaef81a`，后续提交仅补诊断和展示。初版库hash相等假设失败见`preflight-failure.json`，规范化构建路径后的442条编译命令、固定原生提交和串行overlay核对通过。没有重跑normal或故障网络。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/study.py analyze \
  --study /path/to/retained-root/study --output /path/to/new-analysis
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/verify.py \
  --study /path/to/retained-root/study --analysis /path/to/new-analysis/analysis.json --output /path/to/new-proof.json
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/mechanism.py \
  --study /path/to/retained-root/study --output /path/to/new-mechanism.json
```

必须保留原路由、构建/库、输入和原始结果，不能仅靠网页重建真实数据。输出目录/文件要求不存在；不重跑网络即可重算。`delivery_check.py --stage ... --delivery ... --output ...`重放逐包增量、核对确认/机制原行并拒绝四类篡改。`verification.json`保留首版检查，`verification-v2.json`对应兼容全新无修订清单的检查器，未改变运行或展示数据。

## 全新复现补充

先按[方法](README.md)的prepare/normal命令生成独立输入和运行。正常条件后、六个故障前执行：

```bash
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/compatibility.py \
  --study /path/to/new-root/study --old-build /path/to/retained-old-build \
  --new-build /path/to/verified-restore-build --output /path/to/new-root/study/compatibility.json
```

然后依次用相同公开run入口执行`global-w20/local-w20/instant-w20/global-w60/local-w60/instant-w60`，每阶段均由本研究monitor施加预算。新prepare直接写`frozen-v2.json`，不需要制造初版失败或amendment。展示脚本`publish.py --stage ... --output ...`从该根读取已完成分析、原始核验和mechanism；不会执行网络。GPU计算仍未实机校准，不将真实专家选择等同于真实完整推理。
