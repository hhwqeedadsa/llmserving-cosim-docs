# 共享窗口注册扇出与部分失败

本实验核查一个应用窗口如何展开为多个连接的注册句柄，以及注销提前返回后哪些句柄已经退出。它为后续多网卡恢复模型提供语义依据，不提供恢复算法收益。目标和预算见[预登记](protocol.md)，集中结论见[中文报告](../../docs/reviews/共享窗口多连接撤销与部分失败_20261024.md)。

输入只有三份已缓存固定源码：NCCL `12df1a11afad322be5a204a2db890161cbf8131d`的`gin_host.cc`、`gin_host_gdaki.cc`，及DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`的`context.cpp`。源码身份见[来源清单](results/20261024/sources.json)，完整原文与NCCL commit/tree/blob关系随检查点保存；本轮没有下载、provider/GPU/ns-3执行或平台改动。

## 方法和边界

`audit.py prepare`校验缓存来源并保存完整输入；`audit.py build`精确提取3个原函数，与`harness.hpp`、`harness_main.cc`编译成CPU检查程序。引用的函数提取器来自已版本化的`experiments/target_retirement_contract/extract.py`，也随包保存。`audit.py evidence`生成7段固定原文位置；`audit.py analyze`读取CPU记录计算摘要。`verify.py`不调用提取器或检查程序，以整数循环顺序独立核对全部1,120个条件和3类错误结论。

条件为1/2个backend、每backend 1—4个connection、声明1/2个可用设备，包含全成功及逐位置注册报错、空handle、DMABUF能力拒绝、MR注销报错和两阶段元数据清理报错。检查中的4连接/2backend数组是替身容量，不是NCCL生产常数；合成布局不证明现代库实际选择该配置。设备映射沿已核实的`commIdx % nLocalGinDevs`规则声明，未执行真实拓扑选择或建连。

MR/provider结果、GPU元数据和释放是记录替身。`retired`字段只表示本检查中provider注销替身返回成功的句柄集合；它不是实际DMA测量。原始provider失败的真实退出状态可能未知，不能把该字段未出现直接解释为硬件权限仍有效。单连接MR的正常强保证沿用上一轮mlx5来源，不能推广到故障设备或非法并发调用。

## 复核命令

首次收集依赖仓库现有来源缓存，运行目录必须是新目录：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/registration_fanout/audit.py prepare --output /dev/shm/fanout-inputs
```

完整离线复算优先使用本地持久包`runs/registration_fanout_checkpoint_20261024/checkpoint.tar.gz`。先对照同目录`archive.json`核对归档SHA256。包内清单校验一致性，不提供发布者认证。恢复与输出目录必须不存在，父目录必须已存在；解包拒绝重复、非普通文件、越界路径、hash不符或超过16MiB/512成员的包，不执行任何程序。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/registration_fanout/bundle.py restore \
  --archive runs/registration_fanout_checkpoint_20261024/checkpoint.tar.gz \
  --output /dev/shm/fanout-restored

PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/fanout-replay-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 16777216 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/fanout-replayed -- \
  python3 -B /dev/shm/fanout-restored/source/experiments/registration_fanout/bundle.py replay \
  --stage /dev/shm/fanout-restored/evidence --output /dev/shm/fanout-replayed
```

复算需要宿主Python标准库、G++和libc；清除父`PYTHONPATH`，从空cwd重新编译CPU替身，比较生成C++、全部调用记录、7段摘录、摘要和核验结果共5份产物。没有安装或联网步骤。`bundle.py finish`仅供本轮登记的阶段收尾，校验平台连续性和预算、复制持久包与最终收据，不启动下一项实验。

`results/20261024/`保存小结果；1,120行完整记录、三份原文和宿主日志只在检查点中。归档早于恢复和最终预算收据，后者在包外单独保存并绑定hash。本地持久副本不是远程备份；所有成功、失败和边界按实际结果记录，不把CPU控制顺序核查当生产漏洞或新机制。
