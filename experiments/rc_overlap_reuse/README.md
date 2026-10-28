# 忙碌网络中的RC复用与后继依赖

[冻结方法](protocol.md)、[结果网页](results/20261028/report.html)、[文字报告](results/20261028/report.md)、[独立核验](results/20261028/verification.json)。本轮是两个研究候选的有界判别，不是两项论文创新已经成立。

真实1808的一个token/一层专家选择投影为EP4三目的，共43,008B功能数据。显式构造分段通知和前置门槛，只检验依赖/竞争；没有完整DeepEP、Combine、LLMServingSim、GPU或硬件校准。四网络各一次，正常网络均无超时。

- 终端回复丢失：重试开始时全网尚未排空，普通端口/时间非干扰证书仍准入，全部结果吻合。故障连接晚14μs，整个片段仍51.093μs。
- 带后继回复丢失：事前拒绝复用，完整结果50.548→51.794μs。未丢包连接的一次通知提前93ns，逐跳占用等待减少17+4×19ns；最终仅提前2ns。普通RTO平移的整片段误差52ns，不能据此主张显著新模型收益。
- 初次评价后增加源NIC轮询游标的保守约束；执行前源码保留，加固预测与故障前封存逐字节一致。证书是通用强基线，没有完整配对宿主提速证据。

`study.py`独立生成输入并经公开`scaleout rc-fabric-probe`执行，停止账目保存在运行根；`predict.py`只读正常observed，不读truth；`verify.py`独立解包/目标内存重放及逐跳等待核验；`report.py`生成自包含中文HTML与MD；`checkpoint.py`做本地归档和复制源码恢复。研究不导入tests/fixtures或旧实验驱动，平台/依赖未改。

本轮额度关闭，原件在`runs/rc_overlap_reuse_20261028/`。核验已存结果时输出须选新路径，并使用明确资源预算，例如从仓库根目录执行：

```bash
PYTHONPATH=python:. python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /tmp/new-overlap-check-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 268435456 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root runs/rc_overlap_reuse_20261028 \
  -- python3 -B -m experiments.rc_overlap_reuse.verify \
  --stage runs/rc_overlap_reuse_20261028 --output /tmp/new-overlap-verification.json
```

归档与复制源码/空cwd恢复见[收据](results/20261028/recovery.json)和[关闭](results/20261028/closeout.json)。只离线重算预测/核验/报告，没有重新执行包级网络、编译或下载。原始路由、运行时及包级原件仅保存在本地归档，不提交Git。

后续只有在普通资源方法之外存在实质决策差异、或包含缓存准备/检查/回退的完整宿主收益时，才值得转入一般增量算法。不能把0.1%误差、缓存记录数或小功能通过当两项主创新；本轮不扩条件。
