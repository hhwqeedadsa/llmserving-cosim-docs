# 保留应用依赖后的故障影响

本实验使用四份公开推理专家选择的两层、两步保存图，检验故障影响是否进入整个任务窗口完成，而不是只看某条流。实际四个条件已全部完成，本阶段关闭，不增加第五次网络。设计和额度见[事前协议](protocol.md)，结论见[中文报告](../../docs/reviews/应用依赖下的故障影响与无反馈等待_20261024.md)，摘要及核验见`results/20261024/`。

它是旧式按专家贡献复制的声明计算参考，不是现代DeepEP、GPU数值推理或完整61层模型。没有硬件校准、独立留出或算法收益主张。算法观察输入只在`observed/`，原始来源与路由核验在`truth/`。

## 离线复核

持久包位于本地忽略目录`runs/causal_fault_checkpoint_20261024/evidence.tar.gz`；含原始网络JSON、图、来源原件、独立研究源码、失败核验器和日志，不含原生程序及八库。使用Python标准库即可在新源码根、空cwd及空PYTHONPATH复算三份JSON，无需历史前端环境：

```bash
python3 -B experiments/causal_fault/checkpoint.py restore \
  --archive runs/causal_fault_checkpoint_20261024/evidence.tar.gz \
  --output /dev/shm/NEW_OFFLINE_REVIEW
```

恢复前逐成员检查类型、路径、大小和hash，拒绝覆盖目录；比较分析、机制和最终核验逐字节一致。结果见新目录`replay.json`。这只重算旧网络，不能当作两次网络可重复性或跨机完整构建证据。执行前应按协议使用`moe_ub_cosim.host_probe`设置60秒/1GiB RSS/2GiB地址及明确磁盘额度。

## 准备与执行的公开入口

以下说明如何在**新的独立协议与额度**下重建输入，不授权向已关闭阶段追加网络。需要四个本地原件及其manifest、原保存service图、平台代码与`0477866`一致，以及固定依赖和已核hash的stream程序/八库。研究文档提交可以晚于该平台提交。准备程序验证原件与保存图，执行器只调用公开拓扑、`rdma-causal-plan`和`rdma-stream-probe`，输入目录和结果目录必须全新。

```bash
export PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1
python3 -B experiments/causal_fault/prepare.py \
  --repository /home/lry/moe-ub-cosim \
  --saved-service /path/to/saved/service \
  --output /dev/shm/NEW_STAGE/inputs
python3 -B experiments/causal_fault/run.py \
  --repository /home/lry/moe-ub-cosim --stage /dev/shm/NEW_STAGE \
  --backend /path/to/verified/bin/stream-probe
python3 -B experiments/causal_fault/analyze.py \
  --observed /dev/shm/NEW_STAGE/inputs/observed \
  --planned /dev/shm/NEW_STAGE/planned --network /dev/shm/NEW_STAGE/network \
  --output /dev/shm/NEW_STAGE/analysis.json
python3 -B experiments/causal_fault/mechanism.py \
  --network /dev/shm/NEW_STAGE/network --analysis /dev/shm/NEW_STAGE/analysis.json \
  --output /dev/shm/NEW_STAGE/mechanism.json
python3 -B experiments/causal_fault/verify.py \
  --repository /home/lry/moe-ub-cosim --stage /dev/shm/NEW_STAGE \
  --analysis /dev/shm/NEW_STAGE/analysis.json --mechanism /dev/shm/NEW_STAGE/mechanism.json \
  --output /dev/shm/NEW_STAGE/verification-final.json
```

四种条件使用相同工作图和数据量；窗口大于每连接的全部工作，不通过小窗口限速取得收益。组结束包含全部节点和悬空SEND的实际ACK，物理排空另报。分析保留所有并列最晚前驱，但不包含网络跨流资源的完整因果图，不能将该集合当作可消除时延。原协议中一次核验器错误及其修正保留，未重跑网络。
