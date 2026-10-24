# 经典本地处理之后，应用还慢在哪里

本研究固定平台`cf7dd46`，保持四个MMLU请求的两层两步完整保存图、选择重传及全部参数，比较本地剔除、理想零延迟全局改路和首包前预存失效。四次新网络已完成；旧健康/故障两条件仅从固定hash的前阶段归档读取，没有重跑。方法、竞争解释和资源上限见[事前协议](protocol.md)，结果与边界见[中文报告](../../docs/reviews/经典本地处理后的故障损失与乱序重传_20261024.md)。

本地处理将本样本故障增量由178.512us降至79.401us并消除超时；零延迟全局改路反而再慢15.657us。逐包追踪定位到乱序触发重传、共享队列及上层依赖传递。该参考的立即NACK重试没有经典重排容忍，不能把剩余损失当新机制的已证收益，更不能推广到现代DeepEP/生产网络。完整结果和全部76写均保留，不只报告选中的流。

## 离线复核

本地持久包`runs/local_fault_baselines_checkpoint_20261024/evidence.tar.gz`含六条件原始包记录、全部图、四原件、独立研究源码和运行收据，不含native程序/八库。新目录须不存在；按协议使用host_probe设60秒/1GiB RSS/2GiB地址和明确磁盘限额，再执行：

```bash
python3 -B experiments/local_fault_baselines/checkpoint.py restore \
  --archive runs/local_fault_baselines_checkpoint_20261024/evidence.tar.gz \
  --output /dev/shm/NEW_LOCAL_FAULT_REVIEW
```

它先检查全部成员类型/路径/大小/hash，之后才写目录；在新研究源码根、空cwd重算分析、图/网络核验、队列机制及机制核验四JSON，要求逐字节一致，不执行网络。PYTHONPATH仅指新源码，不导入平台、fixture或旧仓库。

## 新阶段重建入口

已关闭阶段不可增加第五次网络。重建需要另立同等或明确新协议，源输入为前阶段固定归档，当前平台代码与`cf7dd46`一致，并有该平台已核hash的stream程序及其构建身份清单。构建身份清单只描述源码/程序/库，不执行平台fixture或历史构建驱动。先审阅脚本，再在全新目录按协议限额运行：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.local_fault_baselines.prepare \
  --repository /path/to/moe-ub-cosim \
  --baseline-archive /path/to/causal_fault/evidence.tar.gz --output /dev/shm/NEW_STAGE/inputs
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.local_fault_baselines.run \
  --repository /path/to/moe-ub-cosim --stage /dev/shm/NEW_STAGE \
  --backend /path/to/bin/stream-probe --build-identity /path/to/build.json
```

`analyze`接收`--observed/--planned/--network/--baseline/--output`；`verify`接收`--stage/--analysis/--output`；`mechanism`接收同样三个参数；`verify_mechanism`另需`--mechanism`。四程序只离线读已生成产物；实际示例及精确命令保存在归档监控和新根`replay.json`中。

分析只读观察图及公开结果；`checks.py`从前研究固定源码继承独立原件/图/字节/时序核验，并将故障时刻断言改为本轮逐条件配置；`routing.py`独立重建BFS候选和固定原生哈希。CNP捕获只直接记录IP，不含fid；该部分按实际生成事件关联stream后使用已固定的fid投影，不能宣称从抓包完整解码CNP。

`mechanism`对所有接收见证队列核算实际端口发送的序列化服务、重传归属和入队时已有数据；重复副本按事后初始副本的丢失/到达状态分类。`verify_mechanism`独立遍历原始记录核对上述字段，不能从这些事实直接推出反事实可避免收益。声明计算、旧贡献复制、四请求/两层、独立DCQCN参考均未硬件校准，论文整体目标继续进行。
