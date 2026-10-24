# 现代provider连接共享范围

逐操作检查现代DeepEP/GDAKI的连接身份，决定旧网络现象能否作为后续优化对象。只使用已有四请求的四次调用和固定源码，不执行网络或GPU。事前范围、唯一声明配置及补充的两个关键来源核查见[协议](protocol.md)。

`prepare.py`核Git树/blob及原件，冻结16条路由选择和8份固定源码；`supplement.py`补核默认counter数为0的第9份源码。一次公开`modern-deepep-plan`产生plan/2。`audit.py queries`执行从原文精确提取的两个标量函数，CPU类型/限定词为替身；486次查询包含本地旁路的通道赋值，并不表示486次实际GIN调用。`audit.py analyze`生成逐操作绑定和共享组。`legacy.py`只读已封存公开图，将76次旧写对应到现代数据操作，固定比较两对已有关键来源。

`verify.py`从原始路由独立枚举所有角色/操作、含本地token的接收序号、上下文和共享组，另重建全部76个旧SEND映射，并拒绝9类篡改。不能从本工具推导网络包数、时延、网卡空闲恢复或安全删除CNP。

本机持久包：`runs/provider_binding_checkpoint_20261024/evidence.tar.gz`。在已有Python环境、仓库根运行：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m experiments.provider_binding.checkpoint restore \
  --archive runs/provider_binding_checkpoint_20261024/evidence.tar.gz \
  --output /tmp/moe-provider-binding-review
```

目标目录必须尚不存在。先逐成员/hash安全核验，再在新研究源码根、空cwd重算分析/关键来源比较/独立核验；三份JSON逐字节一致。恢复不执行公开plan、CPU驱动、网络或GPU，也不依赖旧运行目录。命令本身不是资源限额器，执行时需保持协议的60秒/1GiB RSS/2GiB地址及累计存储限制；重编译CPU驱动所需的源码/原函数和原始命令已在包中，但离线恢复不冒称重新编译或真实provider执行。

结果：[摘要](results/20261024/summary.json)、[来源对应](results/20261024/legacy-comparison.json)、[核验](results/20261024/verification.json)、[恢复](results/20261024/replay.json)、[收尾](results/20261024/closeout.json)及[中文说明](../../docs/reviews/现代连接共享范围与网络优化对象_20261024.md)。所有数字均以“显式声明的配置、四次调用复用同一Context”为前提，不是现代默认配置或真实部署测量。整体论文目标继续进行。
