# 选择重传强基线与故障外溢

本实验在相同真实路由窗口上比较正常/链路失效与go-back-N/选择重传。平台固定`2dc9eb7`，四次研究网络已经执行完毕，不改平台或依赖，不向此前平台阶段追加预算。条件和停止规则见[执行前设计](protocol.md)，结论见[中文报告](../../docs/reviews/强传输基线后的故障外溢_20261024.md)。

## 结果

| 网络 / 传输 | 最后接收us | 最后源确认us | 源数据payload B（含重传） |
|---|---:|---:|---:|
| 正常 / 回退 | 138.887 | 144.955 | 1,070,336 |
| 故障 / 回退 | 159.945 | 163.983 | 1,636,432 |
| 正常 / 选择 | 138.992 | 145.064 | 1,070,336 |
| 故障 / 选择 | 141.539 | 145.579 | 1,530,184 |

故障相对正常的最后接收增量由21.058us降至2.547us，最后源确认增量由19.028us降至0.515us。四组均无超时重传，原生NACK/选择反馈驱动恢复。选择模式正常成本包含50B反馈，回退为48B；声明每连接262,144B乱序payload预算，实际故障下各流峰值之和136,104B，不是物理NIC内存测量。

stream8没有直接丢包、数据/反馈边集合不变，选择模式下仍多等61.213us接收；触发最终接收前缀的包路径相同，源早28ns、队列多61,241ns，精确解释该等待。但它仍不是全窗口最后接收者，不能把61.213us写成整体可消除时间。完整Dispatch—计算—Combine和校准尚缺，以上都不是推理尾延迟收益。

## 实现与复核

- `prepare.py`只在准备时读取四份原始路由，核对hash并生成独立`observed/`与`truth/`。
- `run.py`通过公开拓扑和持久字节CLI运行四条件，保存实际命令、退出和二进制/八库身份；首个失败即停，不重跑。
- `analyze.py`仅读observed和实际包/回调，识别丢包、路径、源重复发送、排队拥有者及已过期副本；选择接收按真实前缀处理，不能沿用回退模式的单包推进假设。
- `verify.py`独立读取/核对原始文件、重建74写，以字节集合/掩码独立验证两种接收、源已知范围、实际反馈、源包与物理跳、FIFO/队列/MMU、每项完成和路径分类，并拒绝5类证据篡改；不导入分析器或平台测试。
- `checkpoint.py`保存原始结果、原始路由、研究源码及终态监控；恢复在创建目录前核对成员路径/类型/集合/大小/hash，再从新源码根/空cwd离线复算两份产物，逐字节比较，不执行网络、不携带原生二进制。

正常和故障回退结果均与前轮完整原生JSON一致（仅八库加载路径不同），并非只看聚合耗时相等。全部74项就绪/发布均0ns；每流整个工作量≤115,712B，小于261,145B选择窗口，实际窗口未限制工作。选择模式增加乱序状态与反馈开销，这些成本保留，不能声称物理资源使用相同。

新研究须先重新声明预算，再使用全新且已有的阶段目录。`$EXP_STAGE`是阶段绝对路径，`$COSIM_STREAM_PROBE`是匹配固定依赖的既有二进制；不安装、下载或重新构建：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/selective_externality/prepare.py \
  --repository . --output "$EXP_STAGE/inputs"
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B experiments/selective_externality/run.py \
  --observed "$EXP_STAGE/inputs/observed" --backend "$COSIM_STREAM_PROBE" --output "$EXP_STAGE/network"
python3 -B experiments/selective_externality/analyze.py \
  --observed "$EXP_STAGE/inputs/observed" --runs "$EXP_STAGE/network" --output "$EXP_STAGE/analysis.json"
python3 -B experiments/selective_externality/verify.py --repository . \
  --inputs "$EXP_STAGE/inputs" --runs "$EXP_STAGE/network" \
  --analysis "$EXP_STAGE/analysis.json" --output "$EXP_STAGE/verification.json"
```

执行时`run.py`检查HEAD等于本研究固定平台版本；新研究提交后若要重建原始条件，应在隔离检出中使用该平台版本及已存研究脚本，不通过改掉版本断言混跑其他平台。离线复核不检查当前HEAD。

原件：`/dev/shm/moe-selective-externality-20261024/`。持久检查点：`runs/selective_externality_checkpoint_20261024/`。Git保存[紧凑结果](results/20261024/summary.json)、[独立核验](results/20261024/verification.json)、[恢复](results/20261024/replay.json)及[预算收尾](results/20261024/closeout.json)。已完成阶段不再追加网络；只恢复/复核已有结果：

```bash
python3 -B experiments/selective_externality/checkpoint.py restore \
  --archive runs/selective_externality_checkpoint_20261024/evidence.tar.gz \
  --output /dev/shm/selective-externality-offline
```

这项经典基线削弱了将“恢复副本导致健康流等待”直接升级为新算法的依据。后续要先确认残余影响是否进入完整应用关键路径，再与恢复限速/公平队列和通信调度等已有方法比较。没有强基线之外的业务价值就结束候选；四份既有路由不证明总体代表性、训练热度、硬件校准或千卡能力。
