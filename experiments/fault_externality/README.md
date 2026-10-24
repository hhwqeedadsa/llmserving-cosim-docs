# 链路失效后的共享队列影响

本实验回答：不经过失效链路、没有直接丢包的专家通信，是否仍会被故障恢复流量拖慢？[执行前设计](protocol.md)固定四个条件，平台为`c8627e8`，未改平台或依赖。[中文结果报告](../../docs/reviews/链路失效后的健康通信与过期重传_20261024.md)解释研究意义和边界。

## 实际结果

四份真实MMLU推理专家路由，各取首轮第4层前8token，实际[8,8,8,6]个token，EP4形成74项远端Dispatch写/1,070,336B。12持久流、1024深度，全部工作0ns就绪并发布，无软件额度等待。8GPU标签/10交换机、100G接入/25G上层、单rail Clos。它是同一网络时钟上的小型通信窗口，不是完整推理或生产部署。

| 网络 / 源恢复 | 全部工作确认（us） | 源数据负载（B，含重传） | 超时重传次数 |
|---|---:|---:|---:|
| 正常 / 固定80us | 144.955 | 1,070,336 | 0 |
| 正常 / Karn80us | 144.955 | 1,070,336 | 0 |
| 失效 / 固定80us | 163.983 | 1,636,432 | 0 |
| 失效 / Karn80us | 164.225 | 1,743,144 | 3 |

失效边8—10在4us关闭，16us集中更新最短路/ECMP。固定超时条件下，5条流直接丢包，另外7条流无丢包且观测到的数据/反馈路径边集合不变；后者4条变慢、3条变快。全部条件无PFC暂停；没有依据把本轮影响归给PFC。

stream8（GPU4→0）从77.026us确认变为145.007us。源负载不变，无重传；最后被接收前缀接纳的数据包路径仍为4→12→15→17→11→8→0，源实际发送仅早32ns，额外接收等待67.330us由队列增加67.362us减源发送32ns精确解释。非排队传输均6.720us。11→8出口等待从0.172us增至55.884us，前面包含flow4/12的重传包；其中56,908B模型帧在该健康包入队时已被源累计确认。

这不是源明知确认仍继续发送的证据。例如flow4序号5000：39.681us发出重传，41.082us收到覆盖ACK，53.274us该副本才到11→8出口排队；健康包80.204us入队时它仍在前面。已发包不能由源收到ACK直接撤回。重复帧字节不等于可消除的业务时间；全窗口最后确认由stream4决定，单独缩短stream8不证明全窗口变快。

源数据负载是模型业务payload，队列中的56,908B包含原生模型头；二者不能混算链路字节。网络finish还要等待控制事件/物理排空，本实验业务指标读取实际acked_ns。

## 代码与复现

- `prepare.py`：核对原文件SHA256，只在准备时读取原始路由，输出`observed/`与冻结路由来源`truth/`。
- `run.py`：只调用公开拓扑/持久流CLI，固定四条件，保留命令、输入/二进制/八库身份及原生监控；失败停止，不重跑。
- `analyze.py`：仅读observed及实际结果。按实际包头关联独立CNP，重建FIFO数据队列、排在前面的发送者和重传次数，生成每流最后接收包的等待分解。
- `verify.py`：独立核算路由投影、逐工作累计接收/ACK、源包、逐物理跳发送/到达时间、全部交换机队列积分/峰值/末态、MMU退出、队列拥有者及已确认旧副本；拒绝ACK时刻、物理到达时刻和重传拥有者三类篡改。不导入分析器、平台测试或旧实验。
- `checkpoint.py`：保存原始记录，检查归档每成员hash，在新源码根/空cwd复算分析与独立核验，逐字节比较；不执行网络，不包含可移植原生二进制。

从仓库根目录执行，`$EXP_STAGE`替换为全新、已存在且有8GiB余量的阶段目录，`$COSIM_STREAM_PROBE`替换为匹配锁定依赖的现有stream_probe。按照设计先设置宿主资源和累计预算，不能复用已有输出名：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/fault_externality/prepare.py \
  --repository . --output "$EXP_STAGE/inputs"
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/fault_externality/run.py \
  --observed "$EXP_STAGE/inputs/observed" --backend "$COSIM_STREAM_PROBE" --output "$EXP_STAGE/network"
python3 -B experiments/fault_externality/analyze.py \
  --observed "$EXP_STAGE/inputs/observed" --runs "$EXP_STAGE/network" --output "$EXP_STAGE/analysis-final.json"
python3 -B experiments/fault_externality/verify.py \
  --inputs "$EXP_STAGE/inputs" --runs "$EXP_STAGE/network" \
  --analysis "$EXP_STAGE/analysis-final.json" --output "$EXP_STAGE/verification-final.json"
```

本轮原始阶段在`/dev/shm/moe-fault-externality-20261024/`，四次网络均已结束，不能向此已收尾阶段追加实验。版本化摘要见`results/20261024/`；持久检查点入口在`runs/fault_externality_checkpoint_20261024/`。

只复核已有记录：

```bash
python3 -B experiments/fault_externality/checkpoint.py restore \
  --archive runs/fault_externality_checkpoint_20261024/evidence.tar.gz \
  --output /dev/shm/fault-externality-offline-review
```

## 对创新和下一步的限制

本轮证明共享队列影响存在，并找到了在途重传副本已经过期的实际事件。不能把拥塞重分布、最短路重建或Karn参数当创新；正常与故障均保留强基线。下一项必须先与选择性重传、合理的恢复流量限速/公平队列、通用通信组调度等基线比较，并说明它们能否在相同缓冲/状态/信息成本下消除现象。没有超过这些基线的效果就结束该候选。

还需完整Dispatch—计算—Combine依赖、独立路由请求、更大负载/拓扑、匹配网卡测量及千卡最终验证。上述条件均未满足，本轮不是A类论文主贡献或生产漏洞证据。也不能从有一个无丢包流变慢，就预设能改善整体推理尾延迟。
