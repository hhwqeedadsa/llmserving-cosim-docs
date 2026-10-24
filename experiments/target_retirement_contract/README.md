# 目标内存撤销与恢复基线核查

本实验回答：取消旧访问权限后，已有写入何时真正退出，安全复用的范围到底是一位专家、一段内存，还是整个注册区？固定平台`a30296c`，本轮不改平台或依赖，不运行ns-3/GPU。结论与边界见[中文报告](../../docs/reviews/目标内存撤销与恢复强基线_20261024.md)，实验规则见[预登记](protocol.md)。

Linux固定v6.12提交`adc218676eef25575469234709c2d87185ca223a`；NCCL固定`12df1a11afad322be5a204a2db890161cbf8131d`；DeepEP固定`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。12份完整源码和获取记录保存在本地检查点，Git中只保存身份与小结果。不要把源码分支行为推广成所有RDMA设备保证。

## 输入和程序

| 程序 | 输入、动作与输出 |
|---|---|
| `prepare.py` | 首次收集用；校验8份本轮API来源、2份旧Linux缓存、2份现代库缓存，形成`inputs/sources.json`及12份原文；依赖当前仓库的数据缓存，不是独立下载器 |
| `extract.py`、`build.py` | 从原文选择12个完整函数，与记录型API替身编译成CPU检查程序；保存行区间、hash、编译命令和替代项 |
| `analyze.py` | 输入原文、编译清单和检查程序JSON；输出16项主张及其原文区间，不接触平台内部真值或网络 |
| `verify.py` | 按保存的行区间独立取字节、核对来源身份与主张边界；拒绝削弱强基线、冒称硬件执行、凭空增加专家撤权、改变key计数四类篡改 |
| `correction.py` | 复核声明/定义定位修复没有改变已编译的12个函数；保存首版分析失败的原因 |
| `checkpoint.py` | 打包或恢复普通文件；恢复前校验全部成员、hash与长度，拒绝越界路径、重复、链接及超额包；恢复不执行程序 |
| `replay.py` | 从恢复后的原文重新编译CPU检查程序，再运行分析、核验和定位修复核对；与原件比较5份产物 |

检查程序使用单线程引用/索引表、确定等待结果、内存数组复制和固件调用记录替身。它执行所选原函数的控制逻辑，没有执行Linux内核、真实DMA、GPU、RDMA provider或网络。它不能证明应用并发使用合法、生产漏洞存在或硬件撤销耗时。

## 离线复核

本轮持久包位置为`runs/target_retirement_checkpoint_20261024/checkpoint.tar.gz`。先对照同目录`archive.json`校验归档SHA256；包内清单只能验证内容一致性，不能提供发布者认证。恢复目标、输出目录必须不存在；不覆盖旧结果，不自动重试失败。

以下命令从仓库根目录运行。`/dev/shm/retirement-review`的父目录必须存在。`replay.py`使用宿主Python标准库、GCC与libc，不安装任何依赖；父`PYTHONPATH`在复算子进程中清除。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/target_retirement_contract/checkpoint.py restore \
  --archive runs/target_retirement_checkpoint_20261024/checkpoint.tar.gz \
  --destination /dev/shm/retirement-review

PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/retirement-review-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 16777216 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/retirement-review-output -- \
  python3 -B /dev/shm/retirement-review/source/experiments/target_retirement_contract/replay.py \
  --evidence /dev/shm/retirement-review/evidence \
  --output /dev/shm/retirement-review-output
```

第一条只解包，压缩/解压内容均限16MiB、成员≤512；第二条受宿主监控并在新工作目录执行。`replay.json`记录原函数生成C、CPU结果、summary、verification、correction的逐字节比较。原始失败日志也随包保存：首次raw请求批次被60秒上限停止，初次分析错误选中前置声明；都不能记为成功。

源码重新获取不是离线复核的前提。`results/20261024/sources.json`给出每份原文的固定版本、URL、长度、SHA256和Git blob身份。包内保存本轮contents API响应和当时获取脚本；原raw脚本有总请求时限缺陷，仅作失败取证，不能作为推荐下载器。重新获取应另设预算和输出目录，不在本轮盲目重取已失败URL。

## 结论如何使用

保留MR（注册内存区）整体撤销为强基线；新查找失败、旧访问退出、源资源释放和应用重启分别处理。RXE快速MR失效与普通USER MR注销不同；mlx5正常撤销的DMA保证不能套用到未核实的错误路径、其他设备或别名注册。共享主窗口的注册粒度比专家粗，允许研究恢复波及范围，但尚未证明其生产代价。下一轮平台要求和实测缺项见报告；本实验不提供恢复收益或论文创新结论。
