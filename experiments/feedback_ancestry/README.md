# 拥塞通知从哪里来

本实验只读上一轮三个修复后应用结果，逐通知连接实际CNP、NP汇总的全部CE包、首次标记出口及其剩余数据队列、应用work字节区间。没有新网络或参数扫描，范围与预算见[protocol.md](protocol.md)。

三条件共248个唯一目的端CE数据包、84个CNP和438个CNP物理发送跳；228个标记点的剩余数据队列中还有其他流。80个CNP到达时相关标记字节已被ACK，31个的相关work也已经完成；这两种现象都不证明反馈错误。正常健康条件16个立即CNP全部满足“标记字节已ACK”，按此条件删除通知会删掉正常反馈。

新的最晚依赖work108在立即改路下继承work90所产生通知后的11.023Gbps声明速率，work132所在连接的最近通知则来自更早work80。来源跨阶段是持久QP的真实状态延续，不是可安全丢弃或免费重置的依据。本工具不是硬件拥塞根因识别器，其他流在队列中也不等于去掉它就能获得相同收益。

## 代码与证据

- `prepare.py`仅接受固定归档，逐成员/hash检查后取三份新结果，并检查本地原论文PDF/text和CP/NP/RP钩子源码身份。
- `analyze.py`只读observed图、planned及公开packet/reference结果，保留每个周期通知的全部成员、首次CE及work相交区间；不读truth或导入平台/fixture/tests。
- `verify.py`用独立通知成员划分、数据队列清单、实际ACK前缀、物理服务与动态BFS/hash核来源。用有理数alpha和整数速率独立重放4,874条RP记录，核原文式1—4的本参考模式，另拒绝11类篡改。它不证明原生Mellanox分支或生产网卡等价。
- `summary.py`生成简明数据；`checkpoint.py`保存选定原件、依据、研究源码及终态监控，在新源码根/空cwd离线复算三份JSON逐字节一致。沿用已核有界研究归档格式，增加实验身份，不含native运行时。

本机持久包为`runs/feedback_ancestry_checkpoint_20261024/evidence.tar.gz`。已有Python环境中，从仓库根恢复到尚不存在的目录：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m experiments.feedback_ancestry.checkpoint restore \
  --archive runs/feedback_ancestry_checkpoint_20261024/evidence.tar.gz \
  --output /tmp/moe-feedback-review
```

成员和hash全部核对后才写文件；恢复不执行网络。按协议配置60秒/1GiB RSS/2GiB地址和磁盘监控，恢复命令本身不是宿主限额器。离线操作不读取旧仓库、平台、动态库或GPU，也不是从零构建/异机校准。

[摘要](results/20261024/summary.json)、[独立核验](results/20261024/verification.json)、[恢复](results/20261024/replay.json)、[预算收尾](results/20261024/closeout.json)与[中文解释](../../docs/reviews/拥塞反馈来源与跨阶段限速_20261024.md)分别保存结论。首次CE后的继续CE不能证明重复标记；capture没有序列化CNP fid，关联采用真实IP/UID和生成记录中的stream/fid，不能称逐bit独立解码。源码保证和实测参数分别处理。

固定4个MMLU请求、两层两步旧贡献复制/声明计算仍未满足现代DeepEP、独立生产负载、硬件校准或完整千卡，研究不自动启动“删除旧CNP”或“每阶段重置QP”的优化。整体论文目标继续进行中。
