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

后续布局核查见[布局与并发槽报告](../../docs/reviews/现代通信布局与并发槽位_20261022.md)。为确认完整put长度及Combine通道依赖，新增独立的六文件来源包；不修改原12文件快照或提高其限额。`--source-cache`仅在files阶段把已验hash的身份/目录复制到新来源包，记录缓存来源，不重复查询树。以下读取仍是每批最多6文件、每响应2MiB/20秒、并发3、整体16MiB/120秒；失败不重试。原件与此前来源均在本机时，审核完全离线。

```bash
timeout 60s python3 experiments/paper_readiness/collect_deepep_modern.py \
  --phase files --source-cache data/research/deepep_modern_20261022 \
  --output data/research/my-deepep-layout \
  --paths deep_ep/include/deep_ep/common/compiled.cuh \
  deep_ep/include/deep_ep/common/math.cuh deep_ep/include/deep_ep/layout/ep/workspace.cuh \
  deep_ep/include/deep_ep/impls/ep/combine_utils.cuh \
  deep_ep/include/deep_ep/impls/ep/dispatch_copy_epilogue.cuh \
  deep_ep/include/deep_ep/impls/ep/combine_reduce_epilogue.cuh
timeout 60s python3 experiments/paper_readiness/audit_modern_layout.py \
  --source-dir data/research/my-deepep-layout \
  --base-source-dir data/research/deepep_modern_20261022 \
  --output runs/my-deepep-layout-audit
```

审核输出11份来源记录/摘录、320种布局核算与对应源码快照，总≤2MiB，CPU60秒/地址2GiB，预留8GiB。不编译CUDA、不执行通信库。H7168/Top8/BF16的Dispatch put为14,464B，选定本地归约Combine put为14,400B，均不能当线上总字节。Dispatch动态slot会影响Combine的通道绑定，后续计划不能提前写死该顺序。

历史现代真实路由研究固定平台`fc5de83`和plan/1，通过当时的公开入口编译相同四原件的entry1/2、层3/4，共四次调用。该版本将self-peer GIN请求误分类为本地旁路；原报告和检查点保留用于版本对照，不能作为当前provider请求统计。以下命令须在该固定版本使用当时脚本，不能用当前脚本重写旧报告。输入由实验自己的准备过程生成，不引用测试配置或平台内部图函数。每rank一个原始请求token、连续专家、BF16及两GIN连接等条件均在provenance中声明，不是实机配置。

```bash
timeout 60s python3 experiments/paper_readiness/modern_operations.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --output /dev/shm/my-modern-real-operations
timeout 60s python3 experiments/paper_readiness/build_modern_operation_review.py \
  --run /dev/shm/my-modern-real-operations \
  --output docs/review_artifacts/my-modern-real-review \
  --checkpoint runs/my-modern-real-checkpoint
```

所有输出目录须不存在。研究最多300秒，单子进程≤60秒/CPU60秒/地址2GiB，RSS验收≤1GiB；产物≤16MiB、卷预留8GiB；外层示例采用更紧的60秒，不自动扩限或重跑。审核检查点原始内容≤16MiB，包含四原件、公开产物、实验脚本和报告，不重复保存第二份plan。来源版本、命令及资源见[现代真实路由报告](../../docs/review_artifacts/modern_operations_20261022/report.html)。

独立枚举486个操作/2,334事件，5个依赖暂扣探针另用反向后继集合核对。38个远端Dispatch在声明配置中全选连接1，38个Combine为连接0/1各23/15；仅为条件源码算术，不是线上利用率。batch1各source/destination的slot组大小均为1，因此本选择没有组内次序歧义；更大batch仍须运行时解析，不能据此固定token顺序。尚未执行现代网络或证明算法收益。

当前复核固定平台`c08cbb9`和plan/2，见[self-peer GIN更正报告](../../docs/review_artifacts/modern_self_gin_20261022/report.html)。沿用四份原件、模型参数和路由选择，独立核算486个操作的API分支，再与已核hash的历史plan/1逐项对照。当前复现使用：

```bash
timeout 60s python3 experiments/paper_readiness/modern_operations.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --output /dev/shm/my-modern-self-gin
timeout 60s python3 experiments/paper_readiness/review_self_gin.py \
  --run /dev/shm/my-modern-self-gin \
  --reference docs/review_artifacts/modern_operations_20261022 \
  --output docs/review_artifacts/my-modern-self-gin-review \
  --checkpoint runs/my-modern-self-gin-checkpoint
```

所有输出目录须不存在。四原件须按manifest恢复；历史归档须按`--reference`目录的`archive.json`恢复至其`path`，并通过SHA256检查，不能拿当前plan替代历史输入。第一个命令执行9条公开CLI并保存资源与退出状态，第二个命令独立核对原始选择、操作差异、全部事件/slot/layout及7个暂扣探针。沿用上节300秒研究、单命令60秒/RSS1GiB/地址2GiB、产物16MiB及预留8GiB预算，不执行网络、不联网或重新下载原件。新检查点包含四原件、当前公开产物、报告及两个研究脚本，不包含后来维护的README、实施规范和任务页；历史归档仍须单独保留。

更正后有364个跨rank GIN请求、80个self-peer GIN请求、42个本地旁路操作；80/444约18%只是GIN API请求比例。跨rank字节量、事件依赖、slot与布局保持不变。self-peer不等于交换机流量，其物理回环路径、NIC服务和完成代价未定义；当前原生事务入口拒绝src=dst，因此不能将本结果写成现代端到端网络执行、时延或优化收益。

校准前可使用只读环境检查，不导入torch、不创建CUDA/RDMA上下文或发送流量：

```bash
timeout 60s python3 experiments/paper_readiness/inspect_calibration_environment.py \
  --python /usr/bin/python3 --output /dev/shm/my-calibration-readiness
```

可重复`--python`明确指定其他已存在的解释器；不扫描全盘、安装工具或启用网卡。输出含原始本地命令日志及去除GPU UUID、网卡GUID/IP等身份字段的`summary.json`。输出目录须不存在，父目录存在，卷预留8GiB；单读取命令≤10秒、整轮≤60秒/RSS1GiB/地址2GiB，产物≤4MiB。命令不可用、超时或未确认的能力保留其状态，不换工具自动重试；不能把环境检查结束理解为性能校准通过。所用固定DeepEP README仍须按来源缓存恢复并通过Git树/blob/SHA256核查。

当前可见6张A100但无Hopper候选，verbs枚举0 HCA且无uverbs节点，4个sysfs端口均Down。rate字段不能作为可用带宽，包元数据缺失也仅限明确检查的Python。实机验证条件与后续实验设计见[校准报告](../../docs/reviews/现代通信校准条件与论文验证路线_20261022.md)。

## 真实路由：共选、相位与专家放置对照

本研究只做离线 Dispatch 数据操作数核算。固定四份原件、EP4、每 rank 一个 Decode token、256 专家/Top8、58 层和 128 个 Decode 位置；八组条件及可保持/改变的因素见[协议](route_controls_protocol.md)。原始与共选交换的专家频次相同，副本数仍从 69,409 变为 79,432—79,600；累计矩阵相同的相位对照也改变窗口分布。详细结果、比值指标的局限和后续网络验证见[HTML](../../docs/review_artifacts/route_controls_20261022/report.html)。

使用 Python 标准库，不安装、不下载、不构建。先按 manifest 恢复本机四份原件；来源和逐文件 hash 见正式结果目录的 manifest.json。原件不是公开 Git 文件，仓库的本地[检查点清单](../../docs/review_artifacts/route_controls_20261022/archive.json)记录其归档和恢复路径。也可将归档中的 data/ 恢复到隔离目录，再将下列 `--source-root` 改为该目录；manifest 中的文件路径按 source-root 解析。

从仓库根执行下面五条研究命令。辅助函数为每条命令施加 60 秒/RSS1GiB/地址2GiB，所有本轮临时产物共同受 64MiB 限制，卷预留 8GiB；准备和分析内部各有 16MiB 输出限制。累计新产物不超过 128MiB。输出目录和核验文件必须不存在；失败保留，不自动重跑或抬限。不要用 `python -O` 禁用脚本中的研究不变量检查。

```bash
COSIM_ROUTE_WORK=$(mktemp -d /dev/shm/moe-route-controls.XXXXXX)
route_budget() {
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_ROUTE_WORK/$1-probe" --artifact-root "$COSIM_ROUTE_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 \
    --artifact-bytes 67108864 --wall-seconds 60 --min-free-bytes 8589934592 \
    -- python3 "${@:2}"
}
route_budget prepare experiments/paper_readiness/prepare_route_controls.py \
  --manifest data/moe-trace-small/manifest.json \
  --config experiments/paper_readiness/route_controls.json \
  --source-root "$PWD" --output "$COSIM_ROUTE_WORK/prepared"
route_budget analyze experiments/paper_readiness/analyze_route_controls.py \
  --observed "$COSIM_ROUTE_WORK/prepared/observed" --output "$COSIM_ROUTE_WORK/analysis"
route_budget repeat-prepare experiments/paper_readiness/prepare_route_controls.py \
  --manifest data/moe-trace-small/manifest.json \
  --config experiments/paper_readiness/route_controls.json \
  --source-root "$PWD" --output "$COSIM_ROUTE_WORK/repeat-prepared"
route_budget repeat-analyze experiments/paper_readiness/analyze_route_controls.py \
  --observed "$COSIM_ROUTE_WORK/repeat-prepared/observed" --output "$COSIM_ROUTE_WORK/repeat-analysis"
route_budget check experiments/paper_readiness/check_route_controls.py \
  --prepared "$COSIM_ROUTE_WORK/prepared" --analysis "$COSIM_ROUTE_WORK/analysis" \
  --repeat-prepared "$COSIM_ROUTE_WORK/repeat-prepared" \
  --repeat-analysis "$COSIM_ROUTE_WORK/repeat-analysis" \
  --source-root "$PWD" --output "$COSIM_ROUTE_WORK/verification.json"
```

`prepared/observed/` 含五份路由张量、四份放置图及八条件索引；`truth/` 含固定配置、来源和交换日志，分析不能读取它。`analysis/windows.csv` 的每行对应一个条件/层/Decode 位置，`matrix_bits` 是 16 个按 source、destination 行优先排列的 0/1 值，含零对角线；一项表示该 source 的 token 是否要向该 destination 发一份数据。份数不是包数，矩阵不含控制流。零远端数据时入量比值为空，不补成 0。summary.json 使用 nearest-rank 经验分位数；窗口相关，不能当独立生产样本。

独立检查读取原件和评价真值，通过另一路逐目的枚举，核对全部矩阵、分位数、专家度数、相位和容量，并比较新目录重复产物；另外输出事后追加的绝对最大入量/比值分母诊断。重复不要求 execution.json 逐字节一致，因为它记录实际路径与资源。上述脚本不导入平台生成器、tests 或 fixture。

生成 HTML 和带逐文件 hash 的本地检查点时，使用已成功完成的五份 probe；结果目录和 checkpoint 目录须位于仓库内且尚不存在：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_ROUTE_WORK/review-probe" \
  --artifact-root docs/review_artifacts/my-route-controls \
  --artifact-root runs/my-route-controls-checkpoint \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 67108864 --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/build_route_controls_review.py \
  --prepared "$COSIM_ROUTE_WORK/prepared" --analysis "$COSIM_ROUTE_WORK/analysis" \
  --repeat-prepared "$COSIM_ROUTE_WORK/repeat-prepared" \
  --repeat-analysis "$COSIM_ROUTE_WORK/repeat-analysis" \
  --verification "$COSIM_ROUTE_WORK/verification.json" --source-root "$PWD" \
  --probe "$COSIM_ROUTE_WORK/prepare-probe" --probe "$COSIM_ROUTE_WORK/analyze-probe" \
  --probe "$COSIM_ROUTE_WORK/repeat-prepare-probe" --probe "$COSIM_ROUTE_WORK/repeat-analyze-probe" \
  --probe "$COSIM_ROUTE_WORK/check-probe" \
  --output docs/review_artifacts/my-route-controls --checkpoint runs/my-route-controls-checkpoint
```

报告生成器复用仓库已有静态 HTML 渲染器，检查本地链接；未作浏览器视觉验收。归档包含原件、两轮准备与分析、五份原始 probe、研究脚本及报告；archive.json 自身和打包过程的 probe 不进入自身归档，打包 probe 另行保存。归档只复现这项离线研究，不包含完整现代网络/GPU 执行环境。

## Prefill放置参考基线与两两统计边界

[协议](placement_baselines_protocol.md)在看到三组放置结果前固定。[结果HTML](../../docs/review_artifacts/prefill_placement_20261022/report.html)区分已有论文的贡献、本轮真实输入核算和人工信息反例。Prefill频次LPT改善计算输入分布，同时增加去重Dispatch份数；不是时延测量。这里只适配作者固定源码的计数LPT，不执行其H100程序，不重现论文加速比。

先从本地[检查点清单](../../docs/review_artifacts/prefill_placement_20261022/archive.json)恢复四原件和固定参考源码。归档路径按仓库根解析；也可恢复到隔离目录，将准备/检查的 `--source-root` 指向隔离目录。公开参考源码的只读获取方法如下；输出目录须不存在，files阶段每次最多两个文件，已有请求失败后不重试：

```bash
timeout 60s python3 experiments/paper_readiness/collect_placement_reference.py \
  --phase identity --commit b059b85e071c03f14482dfbdd736327f41c3f724 \
  --output data/research/my-placement-reference
timeout 60s python3 experiments/paper_readiness/collect_placement_reference.py \
  --phase files --paths zhongkai_test/main_ae.py zhongkai_test/placement_research.py \
  --output data/research/my-placement-reference
timeout 60s python3 experiments/paper_readiness/collect_placement_reference.py \
  --phase files --paths zhongkai_test/run_moe_remap.py README.md \
  --output data/research/my-placement-reference
```

每响应≤2MiB/20秒，全轮≤16MiB/120秒，只读Git API和固定blob，不安装或执行下载代码。脚本保存成功/失败及响应hash，固定commit通过完整tree和blob身份核对。已有正式缓存为 `data/research/placement_reference_20261022`；新路径用于独立恢复时替换下面的 `--reference-sources`。核对还需要前阶段固定Patterns v5的HTML/文本与来源清单，这两份原文也在本轮归档中，不重新下载。

从仓库根执行研究；使用同一预算辅助函数记录每条公开脚本的命令、退出状态与资源。每命令60秒/RSS1GiB/地址2GiB，共享新产物64MiB、累计128MiB，卷预留8GiB；每阶段输出≤16MiB。各输出目录须不存在；失败保留，不提高限额。脚本中的断言是研究不变量，不能用 `python -O` 禁用。

```bash
COSIM_PREFILL_WORK=$(mktemp -d /dev/shm/moe-prefill-placement.XXXXXX)
prefill_budget() {
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_PREFILL_WORK/$1-probe" --artifact-root "$COSIM_PREFILL_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
    --wall-seconds 60 --min-free-bytes 8589934592 -- python3 "${@:2}"
}
prefill_budget prepare experiments/paper_readiness/prepare_placement_baselines.py \
  --manifest data/moe-trace-small/manifest.json --source-root "$PWD" \
  --output "$COSIM_PREFILL_WORK/prepared"
prefill_budget plan experiments/paper_readiness/plan_placement_baselines.py \
  --observed "$COSIM_PREFILL_WORK/prepared/observed" --output "$COSIM_PREFILL_WORK/planned"
prefill_budget evaluate experiments/paper_readiness/evaluate_placement_baselines.py \
  --prepared "$COSIM_PREFILL_WORK/prepared" --placements "$COSIM_PREFILL_WORK/planned" \
  --output "$COSIM_PREFILL_WORK/evaluated"
mkdir "$COSIM_PREFILL_WORK/relocated-observed"
cp "$COSIM_PREFILL_WORK/prepared/observed/frequencies.json" "$COSIM_PREFILL_WORK/relocated-observed/"
prefill_budget repeat-plan experiments/paper_readiness/plan_placement_baselines.py \
  --observed "$COSIM_PREFILL_WORK/relocated-observed" --output "$COSIM_PREFILL_WORK/repeat-planned"
prefill_budget check experiments/paper_readiness/check_placement_baselines.py \
  --prepared "$COSIM_PREFILL_WORK/prepared" --placements "$COSIM_PREFILL_WORK/planned" \
  --repeat-placements "$COSIM_PREFILL_WORK/repeat-planned" --evaluation "$COSIM_PREFILL_WORK/evaluated" \
  --source-root "$PWD" --reference-sources data/research/placement_reference_20261022 \
  --output "$COSIM_PREFILL_WORK/verified"
```

准备程序把entry0统计写入 observed/frequencies.json，未来路由写入 truth/decode.json；放置程序只读 observed。评价输出每条件/层/Decode位置的份数、最大入量、最大计算输入和矩阵；`matrix_bits` 与前节同义，`compute_by_rank` 用冒号分隔四rank计算贡献数。没有时钟、包或准确率输出。独立检查回到原件核算，使用逐rank扫描代替堆生成LPT，并手算验证同单专家/专家对统计却令放置通信量排序相反的人工例子。该例不作模型路由或生产发生率声明。

生成报告和本地归档的命令如下。`--run` 下须有 prepare/plan/evaluate/repeat-plan/check 的probe与上述产物，`--verified` 可指向更正后的成功目录；历史失败probe会保留在归档与执行记录。打包只读取产物，不重跑研究。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_PREFILL_WORK/review-probe" \
  --artifact-root docs/review_artifacts/my-prefill-placement \
  --artifact-root runs/my-prefill-placement-checkpoint \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/build_placement_baseline_review.py \
  --run "$COSIM_PREFILL_WORK" --verified "$COSIM_PREFILL_WORK/verified" \
  --reference-sources data/research/placement_reference_20261022 --source-root "$PWD" \
  --output docs/review_artifacts/my-prefill-placement --checkpoint runs/my-prefill-placement-checkpoint
```

归档不含自身archive.json及尚未结束的打包probe；打包资源记录单独保存。固定原件、作者源码和计算计数可复现，不代表GPU、生产请求或完整现代网络执行可以复现。
