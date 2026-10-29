# 原生服务的业务终态与网络排空

公共`simai-summary`、`rdma-check`、`simai-check`和sweep共用`service_terminal.py`。它仅解释保存结果，不调度网络；有限业务观察结束需由最终COMMIT、前端history和原生waves三方一致支持。失败不能用停止时间补成功时延，缺前端证据则标注不可用，互相矛盾则拒绝。

平台检查覆盖并发图取最后完成而非相加、业务之后的排空、无结果/无前端、失败部分图、15类终态矛盾，以及公共摘要/sweep的一致解释。全套653项CPU单元检查通过；不代表新网络、硬件校准或完整R1性能验证。

六份保存实际服务重读包括LLMServingSim四卡、SimAI六十四卡、320端点正常/失败及后续恢复条件。五份成功记录经原有完整公共验证器核对，失败记录保留业务时间为空；每份成功记录的原生图、最终COMMIT和前端history时间各篡改一次均拒绝，共15个反例。所有原件hash前后相同，没有重跑网络或覆盖旧摘要。精简证据见[verification.json](verification.json)，本机完整日志`runs/service_terminal_20261029/`。

其中固定40正常：业务569.162µs、排空592.996µs；固定80正常：两者均563.347µs。旧`finish_ns`保持原生停止含义，不将约5%的排空改善写成约5%的推理收益。

从仓库根复核保存的实际结果（需要对应运行文件，但不需要旧二进制）：

```bash
PYTHONPATH=python python3 tests/integration/check_service_terminal.py \
  --run runs/review_fixes_20261029/native-smoke/llm-service \
  --run runs/simai_severe_real_20261028/bottleneck25 \
  --run runs/simai_severe_real_20261028/persistent \
  --full-check --output /tmp/moe-service-terminal.json
```

无历史运行目录可用`tests/unit/service_terminal_test.py`中的临时小输入验证公共读取流程，无需外部数据/运行产物。字段和兼容范围见[输出数据](../../docs/record_storage.md)。
