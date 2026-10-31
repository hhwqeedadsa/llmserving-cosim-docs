# 连续Decode窗口与容量结果

[网页](report.html)、[指标](analysis.json)、[来源核验](verification.json)、[现代计划核验](modern-verification.json)、[资源](receipt.json)。本轮没有新网络运行，所有字节和服务量界均为明确表示投影。

在仓库根用下列命令可从保存配置重新生成一个完整现代计划，无需历史runs或原始大文件：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout modern-deepep-plan --format compressed-json --config experiments/route_window_network/results/20261031/inputs/modern-group-0/input.json --output runs/my-real-modern-two-call-plan
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout modern-deepep-check --plan runs/my-real-modern-two-call-plan --output runs/my-real-modern-two-call-check.json
```

输入包含原本15份来源中第一组8个请求的前两步选择；实际源文件身份见inputs/source-manifest.json，远端修订仍未知。完整统计需原件和公共归一化流程，见[事前方法](../../README.md)。frozen和v2保留原身份，不修改旧hash来适配新路径。
