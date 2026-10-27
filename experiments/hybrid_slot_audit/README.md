# Hybrid跨通道slot与逆映射核查

本研究区分“数据存放位置编号改变”和“原token/专家贡献改变”，避免把动态分配本身当作普通任务图无法表达的创新理由。

- [事前协议](protocol.md)
- [中文网页](results/20261027/report.html)、[文本](results/20261027/report.md)
- [研究决定](decision.md)
- [核验](results/20261027/verification.json)、[指标](results/20261027/metrics.json)

固定平台`19670ba`、DeepEP`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。源码中的atomic计数器属于入口GPU本地workspace、按目标GPU分开，同入口跨通道共享。目标缓冲按入口分区；不同入口的slot0不是同一个地址。

六个固定人工输入的150种声明交错全部枚举，各条件得到6/90/24/6/20/4种具体布局，但各只有一种原token/专家贡献关联。两个真实路由前缀条件各三种布局，关联亦保持相同。错误共享全目标计数器和混用旧逆映射均拒绝，六类证据篡改拒绝。

这不是时延等价、CUDA/GPU执行、完整Combine或性能收益。发布时刻、共享QP、actual atomic/内存bank成本、GPU内存序及数值归约均未测。不能把90种布局归一化为一种关联写成90倍仿真加速；普通符号buffer和动态绑定DAG必须作为强基线。

## 代码与输入

`common.py`定义观测输入、固定单通道块序和有界交错。`study.py`准备真实原件与源码、声明交错并构造只读observed的符号参考。`model.py`按源码公式分配slot、按入口分区写数据、建立copy epilogue行号和linked-list，再回溯原token贡献，结果置truth。

`verify.py`从原始专家选择独立构造目标副本和通道顺序，检查声明交错覆盖/顺序、计数器owner、连续slot、地址不重叠、GPU去重、metadata/list和结束标志。它在评价时才读truth。`report.py`生成中文报告，`checkpoint.py`逐成员校验并从复制源码、空cwd离线重算参考、156份声明轨迹及核验/报告。没有研究代码导入tests或fixture，平台没有导入实验。

15完整真实原件来自用户确认的`core12345/MoE_expert_selection_trace/cognitivecomputations/DeepSeek-R1-AWQ/mmlu`，899/1479缺层排除。取entry0/层3每文件前16原token，绑定rank0～14，rank15空；EP16、2域×8GPU、E256/Top8/H7168/BF16，仅检查目标域0，分别2/4通道。没有源到达时间、远端修订校验或生产部署证据。

七份固定源码经Git tree/blob/SHA核对，包含Hybrid Dispatch/Combine、TokenLayout、WorkspaceLayout、copy epilogue、host ep.hpp及32/64B对齐常量；均只读本地缓存，没有执行库或下载。

## 执行与复现

原件、命令、stdout/stderr和资源monitor保存在`runs/hybrid_slot_audit_20261027/`。源码公式解释器是研究模型，并非平台新增的GPU或完整Hybrid执行入口；平台只调用8次公开`scaleout hybrid-deepep-plan`。

阶段顺序为`prepare`、`plans`、`observe`、`reference`、`interpret`。`run.py`通过公开CLI或研究模块执行，在`execution.json`登记实际命令、cwd、退出和日志。阶段只执行一次，拒绝覆盖；本轮额度关闭后不重复运行。如为新研究另行固定预算，可使用以下形式执行一个未开始的阶段：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/NEW_STUDY/monitor/prepare \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 268435456 --wall-seconds 60 \
  --artifact-root runs/NEW_STUDY --min-free-bytes 134217728 -- \
  python3 -B -m experiments.hybrid_slot_audit.run prepare \
  --repository /ABSOLUTE/REPOSITORY --run /ABSOLUTE/REPOSITORY/runs/NEW_STUDY
```

新准备依赖本地固定hash的15原件和七源码；这些大文件不在Git。自包含包为本轮目录的`checkpoint.tar.gz`，身份见`checkpoint.json`，恢复见`recovery.json`。恢复要求明确SHA256，拒绝不安全路径、链接、重复成员、超限或hash不符，使用包内源码离线重算；不需要旧构建、GPU或网络，不重跑公开plan。已经通过的恢复不自动授权再次执行。

本轮预算600受监控秒/256MiB，单命令60秒/RSS1GiB/地址2GiB、128MiB空闲及其中2MiB/10秒管理预留。无新网络、构建、GPU、下载/安装、平台/依赖改动或push。两项论文创新及总目标仍进行中。
