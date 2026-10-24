# 恢复缓冲生命周期审计

本轮不新增网络，只对16份旧原生结果和固定源码核对应用源lease、发送快照与目标去重缓存。发现与后续门槛见[中文报告](../../docs/reviews/有限缓冲恢复与快照模型边界_20261024.md)，范围和预算见[protocol.md](protocol.md)。这不是硬件内存测量或新恢复机制。

在新目录中准备、分析、核验：

```bash
python3 -B experiments/recovery_lifetime/analyze.py prepare \
  --repo /home/lry/moe-ub-cosim --original /ABS/OLD_RECOVERY_EXECUTION \
  --output /dev/shm/NEW_LIFETIME_INPUTS
python3 -B experiments/recovery_lifetime/analyze.py analyze \
  --inputs /dev/shm/NEW_LIFETIME_INPUTS --output /dev/shm/NEW_LIFETIME_SUMMARY.json
python3 -B experiments/recovery_lifetime/verify.py \
  --inputs /dev/shm/NEW_LIFETIME_INPUTS --summary /dev/shm/NEW_LIFETIME_SUMMARY.json \
  --output /dev/shm/NEW_LIFETIME_VERIFICATION.json
```

每个命令整体放在公开`PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe --output-dir NEW_MONITOR --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 33554432 --wall-seconds 60 --artifact-root NEW_OUTPUT_DIRECTORY --min-free-bytes 8589934592 -- ...`后；产物根必须是目录。累计180秒/64MiB，失败保留、不自动扩限。

准备后输入包含16份完整result的无损gzip原件与四份固定源码，无需重新读取旧绝对路径。分析和独立核验仅使用标准库，不导入平台、tests、fixtures或历史研究辅助程序。核验检查捕获/发布及释放/完成对应关系、重复包字节不变与内容量核算，拒绝lease量和容器内容量两种篡改。

`checkpoint.py --stage RUN --output NEW_CHECKPOINT`保存输入、研究源码、监控与摘要，逐文件校验，在新源码根/空cwd离线复算两份JSON并要求逐字节一致。它不执行网络或下载依赖。持久归档入口为`runs/recovery_lifetime_checkpoint_20261024/README.md`；阶段原件位于`/dev/shm/moe-recovery-lifetime-20261024/`。同机持久包不是远程备份。

已完成的[逐条件核算](results/20261024/summary.json)、[独立核验](results/20261024/verification.json)、[源码连续性](results/20261024/source-continuity.json)、[归档恢复](results/20261024/archive.json)和[资源收尾](results/20261024/closeout.json)可直接审查。43成员归档压缩7,355,367B，SHA256为`b12dc66fdaf1550194b4f9e5c1e184cab9da5150c1f854bf3209fa37190cb4b6`；此归档身份是归档后的附录，包内研究代码及原始输入不变。
