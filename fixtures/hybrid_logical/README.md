# 分层逻辑输入与Dispatch门槛验收

2026-10-27；起点`859f0ac`，事前规范`43f21ba`。新增两个公开入口和独立schema，按真实路由可提供的专家ID生成分层需求，不依赖某次网络truth。**本轮功能输入是人工4卡小例，未重新执行15份真实请求研究或新网络。** 使用方法见[示例](../../examples/hybrid_logical/README.md)，便于阅读的结论见[网页](report.html)。

## 已交付与核验

plan保存服务器域、同位置rail、每token跨域数据和域内GPU转发、三个Combine贡献分组、int32计数块和Dispatch周期tail/结束通知。replay检查调用者声明的数据/tail可见性及转发器轮询；一个来源被选中后，不能跳过它去消费其他已就绪来源。

- 8项Hybrid单元检查通过：独立逐expert贡献展开、三种返回分区、112个小宇宙选择/源位置组合、通道有序slot，0/5/6/7/12/13条数据的finish，缓存刷新、二/三域轮询阻塞、错误动作与非法部署/预算。另8项原direct单元通过。
- 新cwd公开流程累计18条CLI，含10类预期拒绝和1次保留的检查脚本失败。人工输入独立应有10条Dispatch、20条域内转发、10条域归约返回、16个计数块和8个通道；其中8个计数块保留GIN self-peer。前缀只允许6条消费，完整门槛回放消费10条；都不表示GPU或collective完成。见[功能核验](verification.json)。
- 重复plan的三个文件及前缀结果逐字节一致；复制整个Python包后在新路径执行相同公开流程，结果逐字节一致。原direct的15条公开命令回归通过，13个固定依赖通过；没有构建或更改依赖。
- 四份固定DeepEP源码的缓存记录、tree/blob、SHA256交叉核对通过；版本`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。见[源码身份](source-basis.json)及[检查程序](check_sources.py)。这是文本依据，不是CUDA、GIN或硬件校准。

首次完整回放检查失败，原因是检查脚本在初次刷新后仍为每个空来源安排一次消费；实际已读到finish的非选中空来源会退出后续轮询。原脚本、原动作、CLI失败日志和monitor保存在`runs/hybrid_logical_20261027/workflow/failed-checker.py`及同目录，原monitor保持`child_failed`。修正检查动作数后仅继续未完成检查，没有重复成功的plan/prefix，平台规则未改。

## 源码与模型的关系

| 内容 | 固定源码位置 | 本轮表示 |
|---|---|---|
| 域计数块 | hybrid_dispatch 175～188 | 两个int32块，含self GIN |
| token按通道/目标域去重 | hybrid_dispatch 336～460 | 有序域通道slot及数据义务 |
| 轮询、等待、每批≤6 | hybrid_dispatch 484～532 | 单转发器可见性/消费门槛 |
| 域内GPU去重与动态slot | hybrid_dispatch 553～647 | GPU集合与贡献来源；最终slot未解析 |
| Combine回放Dispatch元数据 | hybrid_combine 360～393 | 保留Dispatch身份，不安排Combine进度 |
| 三种归约分支 | hybrid_combine 449～576 | Top-K贡献的分区与数据向量操作数 |
| Combine flush/结束/消费清零 | hybrid_combine 589～639 | 明确未执行；不称完整协议 |

转发门槛中的“快照”是调用者声明的稳定观察点，不估计GPU轮询耗时。跨转发器顺序、atomic slot、linked-list、TMA完成、barrier、源缓冲生命周期、ACK/CNP和故障恢复仍未接入。TokenLayout操作数字节不等于实际包或全网字节，Combine数据分组不验证浮点数值等价。

## 恢复与资源

原件`runs/hybrid_logical_20261027/`。包`checkpoint.tar.gz`包含397个成员，恢复核对各成员SHA256后，从复制源码和空cwd执行plan及两次回放，全部结果逐字节相同，零网络。见[恢复核验](recovery.json)。SHA256：

```text
d4b1a318b30124fea24adf891802dce19f34b3327802375ce2d4a3ce75687467
```

在仓库根目录可用下面的独立程序恢复；目标目录必须不存在，仍需按[规范](../../docs/tasks/hybrid_logical_spec.md)监控资源：

```bash
python3 fixtures/hybrid_logical/checkpoint.py \
  --archive runs/hybrid_logical_20261027/checkpoint.tar.gz \
  --sha256 d4b1a318b30124fea24adf891802dce19f34b3327802375ce2d4a3ce75687467 \
  --output /tmp/hybrid-restored
```

11个主monitor全部结束，保留1个原检查失败；累计5.977受监控秒、采样组RSS峰值40.3MiB、原件约9.29MiB分配空间，另在128MiB额度中保留2MiB管理空间。单命令60秒/RSS1GiB/地址2GiB，累计300秒。完整收据见[收尾](closeout.json)。零新网络、GPU、构建、下载、安装或push，仅本地保存。

## 对研究目标意味着什么

它补齐了把direct直接放大到较大EP时缺失的一部分通信语义；分层通信和轮询规则来自已有DeepEP，不能写成新的网络算法。ASTRA-sim等平台也可以承载用户提供的正确分层算法，不能把本接口当作竞品必然缺少的功能。平台差异与两个创新候选见[研究评估](../../docs/reviews/联合仿真的两个创新候选与网络收益判别_20261026.md)。

下一项有价值的验证是：在正确分层模型和完整DAG强基线下，检验紧凑表示是否减少完整工作成本，以及保留进度/消费门槛能否安全减少包细节处理。完整Hybrid执行、真实provider成本、独立请求泛化和净收益仍待证明；本轮没有测得这两项贡献，也没有千卡完成或生产收益结论。总体目标继续进行中。
