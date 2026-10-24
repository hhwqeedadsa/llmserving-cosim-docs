# 窗口生命周期与合法恢复边界

本研究核查固定NCCL/DeepEP的用户窗口、底层注册复用及注销错误传播，为后续恢复模型排除不合法或过弱的基线。规则见[预登记](protocol.md)，结论见[中文报告](../../docs/reviews/窗口注销合法边界与底层注册复用_20261024.md)。平台保持`08bf585`，没有GPU/provider/ns-3执行或依赖修改。

`sources.py --output NEW_DIR`复用六份固定缓存并取得五份小Git blob，`sources.py --supplement --output SAME_DIR`取得第六份使用文档；各请求最多8秒且不重试。来源准备依赖仓库缓存，完整离线复核应使用本轮检查点，避免对已经完成的来源阶段重新获取。12份原文逐大小/Git blob/SHA256核验，响应和版本关系全部保留。

`audit.py build --inputs INPUTS --output NEW_BUILD`精确提取四原函数并以G++/C++17严格编译；`NEW_BUILD/source-harness`向stdout写出432条CPU记录。provider、CUDA、shadow、列表状态及释放均为记录替身；没有构建或运行真实NCCL、上游构建脚本或下载二进制。`audit.py evidence`生成14份原文摘录，`audit.py analyze --records RECORDS --output SUMMARY`计算分组。`verify.py`不导入提取器或harness，以独立整数规则核对全部调用顺序、引用数、逐段访问和公开返回值，并拒绝四种错误结论。

```bash
python3 -B experiments/window_lifecycle/bundle.py restore \
  --archive runs/window_lifecycle_checkpoint_20261024/checkpoint.tar.gz \
  --output /dev/shm/window-lifecycle-restored

PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/window-lifecycle-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 16777216 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/window-lifecycle-recomputed -- \
  python3 -B /dev/shm/window-lifecycle-restored/source/experiments/window_lifecycle/bundle.py replay \
  --stage /dev/shm/window-lifecycle-restored/evidence \
  --output /dev/shm/window-lifecycle-recomputed
```

包内manifest提供一致性核验，不是发布者认证；恢复拒绝重复、非普通文件、越界路径和超过16MiB/512成员的包，不执行程序。复算从空cwd和新源码根重新编译CPU原函数，比较生成源码、全部记录、摘录、摘要和核验共五份产物。需要已有Python标准库、G++及libc，无安装或联网步骤。同Linux宿主复算不证明跨平台构建或硬件行为。

`results/20261024/`保留小结果；原始记录、完整来源、响应和全部命令/日志存检查点。`archive.json`绑定包，`replay.json`与`closeout.json`是打包后的独立收据，本地持久副本不等于远程备份。返回成功与真实退出、元数据错误与DMA、合成条件数与生产概率分别解释；当前没有证明应用故障并发合法性、生产漏洞、性能收益或论文创新。
