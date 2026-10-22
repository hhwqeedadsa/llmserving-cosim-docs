# 论文基础审计与强基线判别

首个问题是：此前观察到的源端资源传播，在去掉未经硬件校准的软件字节/槽限制后是否仍存在？本实验筛查机制依赖性，不设计新策略、不以正收益为通过标准。

固定此前8GPU合成MoE层段、相同路由、计算和100G Clos；batch/rank1、4。每个batch四种准入：3槽/16384B；3槽/无字节上限；64槽/无字节上限；软件透传。各模式无故障与单最终ACK丢失一次，共16条件。恢复使用正常40μs初值，历史与目的端份额均关闭。透传仅取消外加软件额度，原生网卡链路和网络协议仍执行，不等于无限硬件。

先经公开sweep执行两组正式无故障条件，从各自实际首图选择0→4 Dispatch，再执行其余14组；不假定不同batch的目标字节相同。分析分别报告目标ACK、健康后续准入、前端图完成和网络排空；路径/共同图按实际结果核对。

```bash
PYTHONPATH=python python experiments/paper_readiness/run.py \
  --backend "$COSIM_NATIVE_SERVICE" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/moe-paper-readiness-20261022
python experiments/paper_readiness/analyze.py \
  --input /dev/shm/moe-paper-readiness-20261022 \
  --output /dev/shm/moe-paper-readiness-analysis-new
```

使用已有Python环境和构建，不安装/下载依赖。每条件沿用60秒/RSS1GiB/地址2GiB/仿真5ms；每次清单300秒/64MiB、总研究128MiB，卷预留8GiB。失败保留，不自动重跑、扩限或扩大矩阵。研究使用自己的配置副本，不导入tests/fixture/私有驱动。后续需要留出真实请求、真实通信库语义和硬件校准才能判断论文贡献。

2026-10-22结果见[强基线判别](../../docs/review_artifacts/paper_readiness_20261022/screening.html)，总体评价见[论文评估](../../docs/review_artifacts/paper_readiness_20261022/report.html)。移除字节额度不改变整体完成，但batch4局部准入/消息时序有变化；非约束并发与透传的执行证据一致，软件准入等待为0。故障影响在透传下仍存在。结果不证明真实硬件存在3槽限制，也不作为新算法收益。

`collect_standards.py`只读取固定的两份RFC和两页官方征稿，逐页2MiB、20秒，失败记为unavailable；无自动重试。已有15份相关工作原文复用本机固定快照并重新核hash，没有重新下载。评估状态在当前任务页维护，不将本轮判别完成等同论文研究完成。
