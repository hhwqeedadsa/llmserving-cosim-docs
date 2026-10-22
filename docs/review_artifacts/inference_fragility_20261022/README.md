# 推理通信脆弱性：本轮审阅入口

先看[整体改进HTML](architecture.html)，再看[八组实验及包级证据](report.html)和[条件证明HTML](formal.html)。[整体报告Markdown](../../reviews/推理通信脆弱性与形式化改进_20261022.md)、[数值Markdown](results.md)与[详细数据](summary.json)供复核。

平台实现见本地提交`5e092a1`；[功能门](../../../fixtures/scaleout_adaptive/README.md)与[独立研究](../../../experiments/inference_fragility/README.md)分别验收。HTML由`build_review.py`和独立研究`analyze.py`生成，仅使用Python标准库，无外部网页资源。当前仅做内容、确定性和本地链接检查，没有浏览器视觉验收。

本轮完整原始运行位于`/dev/shm/moe-fragility-20261022/`。持久证据保存在仓库忽略目录`runs/inference_fragility_20261022/evidence.tar.gz`，校验入口见[归档清单](archive.json)和[完成核对](completion_audit.md)。它们包含成功与预期失败，不覆盖上一阶段记录。

恢复研究原始产物后，执行以下命令可以重做离线分析，无需再仿真：

```bash
python experiments/inference_fragility/analyze.py \
  --input "$EVIDENCE_ROOT/experiment" \
  --capture "$EVIDENCE_ROOT/packet-capture" --output /dev/shm/fragility-reanalysis
python docs/review_artifacts/inference_fragility_20261022/build_review.py
```

`EVIDENCE_ROOT`是解压后包含`experiment/`的本轮运行根；分析输出要求是新目录。完整重新执行的公开命令、预算与参数见独立研究README。归档保存构建来源和旧比较二进制；当前二进制需按锁定依赖与源码重新构建，不能把这份证据包当成可移植运行环境。

`audit_evidence.py`只调用公开诊断命令重新核对已有结果，不执行仿真；参数为`--run-root`、`--build-provenance`、`--output`。它还检查执行/构建来源、离线分析一致性和HTML本地链接。恢复到其他目录时使用保存的来源快照或对应提交；审计工具以当前检出的源码核对来源，不自动切换版本。
