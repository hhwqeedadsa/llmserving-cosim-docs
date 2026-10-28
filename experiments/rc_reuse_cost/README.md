# RC结果复用的完整宿主成本

[方法与预算](protocol.md)、[网页](results/20261028/report.html)、[文字报告](results/20261028/report.md)、[独立核验](results/20261028/verification.json)。**计划三对，只完成一对，比较未完成。** 核验器的取整错误触发事前停止条件，原始测量/源码/失败均保留；修正后仅离线检查，没有补跑。

新输入为6447路由的单token/层片段，EP4，三目的共43,008B功能数据；MTU4096、RTO25us、100G/1us，固定速率RC。两个程序各三种不同Atomic ACK丢失；分段/门槛是人为微基准，没有完整现代DeepEP、MoE服务、LLMServingSim、GPU或硬件校准。

首对完整流程包括启动/校验/哈希、公共CLI、内部监控、正常缓存准备、检查/回退和相同摘要输出。direct六次故障；reuse两次正常加三次回退，三项终端查询直接复用。11个实际网络全部经独立字节/目标重放核对，六项canonical结果相同。

| 首对 | direct | reuse | direct/reuse |
|---|---:|---:|---:|
| CPU/秒 | 1.223194 | 1.127568 | 1.085 |
| 墙钟/秒 | 1.618885 | 1.453902 | 1.113 |

只完成direct→reuse一对，操作系统页缓存未清空，不能评价预定三对门槛或声称稳定收益。完整成本线索不等于MoE独有创新；经典资源复用纳入强基线。

`study.py`独立准备输入并用wait4测完整工作流；`workflow.py`经公开`scaleout rc-fabric-probe`执行平台，仅读自身正常缓存的固定算法源码；`verify.py`独立重放原始包/内存并核CPU/墙钟与输出；`report.py`生成中文MD/HTML；`replay_predictions.py`仅离线重算判断；`checkpoint.py`保存和恢复。没有导入tests、fixtures或历史测量驱动。复用算法固定自`5f7adb4`的`rc_overlap_reuse/predict.py`，不使用其旧输入/驱动。

原件在`runs/rc_reuse_cost_20261028/`。本轮网络/性能额度关闭，后四工作流保持未执行，不能通过measure命令继续。只核验已存数据时使用新输出路径和明确资源预算，例如从仓库根目录：

```bash
PYTHONPATH=python:. python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /tmp/new-reuse-cost-check-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 536870912 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root runs/rc_reuse_cost_20261028 \
  -- python3 -B -m experiments.rc_reuse_cost.verify \
  --stage runs/rc_reuse_cost_20261028 --output /tmp/new-reuse-cost-verification.json
```

[失败记录](results/20261028/failure.json)、[归档恢复](results/20261028/recovery.json)及[关闭](results/20261028/closeout.json)明确实际范围。复制源码/空cwd仅重算六个判断、已完成一对的指标/核验及报告，逐字节相同；恢复没有执行网络或重测性能。原始路由、运行时及完整包日志只在本地归档，不提交Git。
