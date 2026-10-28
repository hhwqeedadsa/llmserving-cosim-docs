# 2026-10-28 重度丢包推理实验离线证据

`evidence.tar.gz` 保存了两批原始运行的逐包抓包、任务图、运行配置、分析输出，以及实验脚本和相关源码，共405个文件、25,979,870字节（未压缩）。临时构建目录未归档。

- SHA-256：`c3c3f3abe57a6cdf47b7ff3292a2650339eb50b1c067f5651d2a97c47b3d3a09`
- 归档与恢复：`python3 experiments/heavy_loss_inference/preserve.py restore --archive <evidence.tar.gz> --output <new-empty-directory>`
- 本次恢复校验通过，恢复405个文件；没有重跑网络仿真或重新构建。
- 实验结论及适用边界见 [复盘报告](../../reviews/20261028_丢包实验与周期工作复盘.md) 和 [网页报告](../../../experiments/heavy_loss_inference/results/20261028/report.html)。
