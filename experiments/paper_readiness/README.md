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

后续G1已完成固定DeepEP v1.2.1的九份源码/说明核验与四请求离线核算，见[通信语义审计HTML](../../docs/review_artifacts/deepep_semantics_20261022/report.html)。当前模型没有声称复现DeepEP；本轮具体说明哪些差异阻止将抽象实验推广到其低延迟路径，没有改变网络执行。

```bash
python3 experiments/paper_readiness/audit_deepep.py \
  --source-dir data/research/deepep_semantics_20261022 \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --graphs /dev/shm/moe-real-four-20261018/service \
  --output /dev/shm/my-deepep-audit
```

`--graphs`可省略。脚本只读标准源码与公开保存的wave，不调用平台内部生成器；数据量核算与网络包执行分开。所有输出目录需为新目录。固定源码获取命令如下，只读GitHub Git API，七个blob并发最多3、每响应2MiB/20秒、总120秒/16MiB，不安装、不执行源文件，失败不自动重试：

```bash
timeout 120s python3 experiments/paper_readiness/collect_deepep.py \
  --phase tree --output data/research/deepep_semantics_20261022
timeout 120s python3 experiments/paper_readiness/collect_deepep.py \
  --phase files --paths csrc/deep_ep.cpp csrc/deep_ep.hpp \
  csrc/kernels/buffer.cuh csrc/kernels/internode_ll.cu \
  csrc/kernels/ibgda_device.cuh csrc/event.hpp deep_ep/utils.py \
  --output data/research/deepep_semantics_20261022
```

另两份`README.md`、`deep_ep/buffer.py`复用`data/research/hardware_systems_20261020/`中固定blob缓存，身份由版本化来源清单及同一完整Git树共同验证；不能用main文件代替。恢复来源缓存、四份路由和两份既有wave的本地检查点见[归档清单](../../docs/review_artifacts/deepep_semantics_20261022/archive.json)，它不是完整生产实验或论文完成证据。

独立检查示例（`--repeat`指相同输入另一个新目录的离线核算，不必重跑网络）：

```bash
python3 experiments/paper_readiness/build_deepep_review.py \
  --output docs/review_artifacts/deepep_semantics_20261022
python3 experiments/paper_readiness/check_deepep_audit.py \
  --audit docs/review_artifacts/deepep_semantics_20261022/audit.json \
  --repeat /dev/shm/my-deepep-audit/audit.json \
  --html docs/review_artifacts/deepep_semantics_20261022/report.html \
  --graphs /dev/shm/moe-real-four-20261018/service \
  --output /dev/shm/my-deepep-verification.json
```

wave路径按公开运行目录内的相对位置记录。恢复归档后，可把核算与独立检查的`--graphs`均指向解压后的`saved_run/`；来源缓存仍按清单相对仓库根的路径恢复。相同输入的离线核算要求逐字节一致，迁移后的两份wave也按原hash核对。检查点只含核算需要的公开JSON，不含重新执行整个网络所需的全部二进制与产物。

真实路由操作研究使用平台`0cc140f`的公开离线入口，见[结果报告](../../docs/review_artifacts/deepep_operations_20261022/report.html)。它从同四份原件选取两层、两步，共四次MoE调用，声明EP4、BF16、等量连续专家、单NIC且关闭跨rank P2P。没有执行完整模型或网络，不提供时延与吞吐预测。

```bash
python3 experiments/paper_readiness/deepep_operations.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --output /dev/shm/my-deepep-real-operations
python3 experiments/paper_readiness/build_operation_review.py \
  --run /dev/shm/my-deepep-real-operations \
  --output docs/review_artifacts/my-deepep-operation-review \
  --checkpoint runs/my-deepep-operation-checkpoint
```

输出目录均须不存在。第一个命令独立核算操作与原件、执行六条公开命令并保存Linux wait4资源统计；单命令60秒/RSS1GiB/地址2GiB，整轮300秒/64MiB新产物，预留8GiB。第二个命令核对固定源码hash、逻辑上下文和反向后继可达性，验证三个重复产物及本地HTML链接，生成不超过32MiB原始内容的本地tar检查点。报告正文的证据链接指向本仓库版本化的正式结果目录；自定义目录用于复核，不改写正式结果。归档不重复保存第二份plan，恢复来源和平台后可用公开命令重新生成。当前结果为8,448操作/33,968事件，零count仍是必要依赖；不证明通知是性能瓶颈，也不验证源buffer、物理QP、现代DeepEP内核或硬件包级行为。

现代候选的12份固定源码和原始选择核对见[后端实现约束](../../docs/review_artifacts/deepep_modern_20261022/report.html)。现代direct按目的rank去重、批量交换计数，并有明确核心kernel屏障；不以v1作为唯一强基线。`collect_deepep_modern.py`只读GitHub Git API，不安装库或更改依赖。首次发现可不传commit，正式复现必须指定固定提交：

```bash
timeout 60s python3 experiments/paper_readiness/collect_deepep_modern.py \
  --phase tree --commit f6aa38fe06cd4a0489e7730365d4e0114970a2f3 \
  --output data/research/my-modern-source
timeout 60s python3 experiments/paper_readiness/collect_deepep_modern.py \
  --phase files --output data/research/my-modern-source \
  --paths README.md csrc/buffers/ep.hpp \
  deep_ep/include/deep_ep/comm/handle.cuh \
  deep_ep/include/deep_ep/impls/ep/dispatch.cuh \
  deep_ep/include/deep_ep/impls/ep/combine.cuh \
  deep_ep/include/deep_ep/impls/ep/hybrid_dispatch.cuh
timeout 60s python3 experiments/paper_readiness/collect_deepep_modern.py \
  --phase files --batch completion --output data/research/my-modern-source \
  --paths deep_ep/include/deep_ep/comm/barrier.cuh \
  deep_ep/include/deep_ep/impls/comm/barrier.cuh \
  csrc/kernels/comm/context.cpp csrc/kernels/comm/symmetric.hpp \
  deep_ep/include/deep_ep/layout/ep/token.cuh \
  deep_ep/include/deep_ep/impls/ep/hybrid_combine.cuh
timeout 60s python3 experiments/paper_readiness/audit_deepep_modern.py \
  --source-dir data/research/my-modern-source \
  --output docs/review_artifacts/my-modern-review \
  --checkpoint runs/my-modern-checkpoint
```

原始路由和前阶段公开input仍须从对应检查点恢复。每响应2MiB/20秒、最多3并发，两个文件批次总12份，连同目录查询累计≤16MiB/120秒；失败和重复批次拒绝，不能用新批次名重试同一失败文件。离线审核的检查点≤4MiB原始内容，卷预留8GiB。源码调用核算和实际包、时延、设备支持严格分开；该次现代DeepEP核查尚未包含NCCL provider，后续结果见下文。源码采集失败不会降级成其他版本。两次报告构建分别发现自引用检查顺序和迁移导航路径错误，已修正并保留失败；最终跨目录复核通过，源码证据与选择核算两个JSON逐字节一致。实际命令、退出状态和归档复核见[执行记录](../../docs/review_artifacts/deepep_modern_20261022/execution.json)，没有重跑网络。

固定NCCL v2.32.3-1的GIN/GDAKI源码核查与四请求全部Decode路由核算见[报告](../../docs/review_artifacts/nccl_gin_20261022/report.html)。只核算fresh-routing direct Dispatch的数据操作数，不产生包或时延。18份NCCL来源使用完整Git树/blob/SHA256验证；缓存可从[检查点](../../docs/review_artifacts/nccl_gin_20261022/archive.json)恢复，也可按下列固定tag读取公开源码。首次身份查询解析tag到提交，正式审核严格核对`12df1a11afad322be5a204a2db890161cbf8131d`，不能替换版本。

```bash
timeout 60s python3 experiments/paper_readiness/collect_nccl_gin.py \
  --phase identity --tag v2.32.3-1 --output data/research/my-nccl-gin
timeout 60s python3 experiments/paper_readiness/collect_nccl_gin.py \
  --phase files --output data/research/my-nccl-gin \
  --paths docs/userguide/source/api/device_gin.rst src/include/nccl_device/gin.h \
  src/include/nccl_device/impl/gin__funcs.h src/include/nccl_device/gin/gdaki/gin_gdaki.h \
  src/transport/net_ib/gdaki/gin_host_gdaki.cc src/gin/gin_host.cc
timeout 60s python3 experiments/paper_readiness/collect_nccl_gin.py \
  --phase files --batch queue --output data/research/my-nccl-gin \
  --paths src/include/nccl_device/core.h \
  src/transport/net_ib/gdaki/doca-gpunetio/include/device/doca_gpunetio_dev_verbs_qp.cuh \
  src/transport/net_ib/gdaki/doca-gpunetio/include/device/doca_gpunetio_dev_verbs_cq.cuh \
  src/transport/net_ib/gdaki/doca-gpunetio/include/device/doca_gpunetio_dev_verbs_onesided.cuh \
  src/include/nccl_device/gin/gin_device_common.h src/transport/net_ib/gin.cc
timeout 60s python3 experiments/paper_readiness/collect_nccl_gin.py \
  --phase files --batch contract --output data/research/my-nccl-gin \
  --paths src/include/nccl_device/host.h \
  src/transport/net_ib/gdaki/doca-gpunetio/include/common/doca_gpunetio_verbs_def.h \
  plugins/gin/README.md src/include/nccl_device/impl/core__funcs.h \
  src/devcomm/devcomm_v23000.cc src/include/nccl_device/gin/gdaki/gin_gdaki_device_host_common.h
```

每批最多6份、并发3、每响应2MiB/20秒，身份与三个批次累计≤16MiB/120秒；失败不提高限额或换批次重取。以下离线操作不联网。所有输出目录需不存在，使用已有四原件和现代源码缓存；源码审核还读取前阶段版本化来源清单。manifest和原件路径按仓库根恢复，不是任意模型参数扫描工具。

```bash
timeout 60s python3 experiments/paper_readiness/direct_route_accounting.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --output /dev/shm/my-direct-routes
timeout 60s python3 experiments/paper_readiness/audit_nccl_gin.py \
  --source-dir data/research/my-nccl-gin --routes /dev/shm/my-direct-routes \
  --output runs/my-nccl-review --checkpoint runs/my-nccl-checkpoint
```

每命令60秒/RSS1GiB/地址2GiB；路由结果≤8MiB，审核归档原始内容≤8MiB，磁盘预留8GiB。输出包括逐窗口CSV、来源、上下文映射、HTML及hash核验的本地tar。另将核算输出到第二个新目录，再将审核输出到另一仓库内目录，执行：

```bash
timeout 60s python3 experiments/paper_readiness/check_nccl_gin.py \
  --review docs/review_artifacts/nccl_gin_20261022 \
  --repeat-routes /dev/shm/my-direct-routes \
  --relocated runs/my-nccl-review \
  --output runs/my-gin-repeat-check.json
```

该检查读取两份归档并逐文件核hash，比较三个路由结果与六个跨目录稳定结果，检查两份HTML本地链接。执行资源字段与相对导航允许不同；没有浏览器视觉验收。正式结果覆盖7,424窗口、118,784矩阵项，现代去重后的69,409份跨rank token仍有目的rank不均，但不能由此推断拥塞或加速。后续实现要求集中在[现代后端规范](modern_backend_contract.md)，尚未接入网络。
