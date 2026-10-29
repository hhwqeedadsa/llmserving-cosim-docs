# 真实路由服务的恢复强基线

先读[冻结方法](protocol.md)。本研究在320端点有限MoE推理窗口上比较已有固定80µs/Karn80µs，在正常/短窗50%目标数据丢失下共四次新网络；旧固定40µs的两个条件仅只读复用。不是新重传算法，不自动重开旧实验。

准备旧研究完整运行目录、相同既有原生service二进制、现有SimAI依赖环境和protoc。使用公开SimAI运行入口；不下载、不构建，不导入tests或fixture执行驱动。`run.py`要求工作树干净、输出不存在，检查旧数据和二进制hash；首次失败后停止。

```bash
PYTHONPATH=python python -m moe_ub_cosim.host_probe \
  --output-dir runs/service_recovery_baselines_20261029/host \
  --artifact-root runs/service_recovery_baselines_20261029 \
  --rss-bytes 2147483648 --address-bytes 4294967296 \
  --artifact-bytes 536870912 --wall-seconds 600 --min-free-bytes 8589934592 \
  -- python experiments/service_recovery_baselines/run.py \
  --baseline-root /path/to/simai_severe_real_20261028 \
  --backend /path/to/existing/native-service --protoc /path/to/protoc \
  --output runs/service_recovery_baselines_20261029/study

python experiments/service_recovery_baselines/analyze.py \
  --study runs/service_recovery_baselines_20261029/study \
  --baseline-root /path/to/simai_severe_real_20261028 \
  --output /path/to/new-analysis
```

分析不运行网络。它独立核对所有消息的包级字节和首次确认，要求对应输入/拓扑/工作图一致；正常与故障按各自机制成对报告。输出JSON、中文Markdown和可直接打开的HTML，不使用用户原始prompt内容作页面展示。
