# 论文基础审计与强基线判别

跨窗口/ECMP网络判别使用固定四请求、层4/32的Decode入口5/6、三放置、两上层速率与三个种子，共18条件。方法见[执行前协议](dispatch_validation_protocol.md)，结果见[HTML报告](../../docs/review_artifacts/dispatch_validation_20261022/report.html)。窗口此前已有离线统计，仅新增包级评价，不称独立留出。结果把总数据量、端点负载与已知链路服务量基线分开，不以此申明新放置算法或现代完整通信库。

使用已有原生stream-probe和新的输出父目录，设置本仓库绝对`PYTHONPATH`。每一步按协议用公开`python -m moe_ub_cosim.host_probe`包住：60秒/RSS1GiB/地址2GiB，运行器64MiB，其余32MiB，全部累计128MiB/300秒、卷预留8GiB；正式命令见报告维护记录。placement原件可从[前阶段检查点](../../docs/review_artifacts/prefill_placement_20261022/archive.json)的`saved_run/planned/placements.json`恢复，本次检查点也保存同一原件和原始四请求。

```bash
python experiments/paper_readiness/prepare_dispatch_validation.py \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --placements /path/to/hash-checked/placements.json --output /dev/shm/my-dispatch-validation-new
python experiments/paper_readiness/run_dispatch_validation.py \
  --observed /dev/shm/my-dispatch-validation-new/observed \
  --backend /absolute/path/to/native-stream-probe --output /dev/shm/my-dispatch-validation-new/run
python experiments/paper_readiness/analyze_dispatch_network.py \
  --observed /dev/shm/my-dispatch-validation-new/observed --run /dev/shm/my-dispatch-validation-new/run \
  --output /dev/shm/my-dispatch-validation-new/analysis
python experiments/paper_readiness/analyze_dispatch_network.py \
  --observed /dev/shm/my-dispatch-validation-new/observed --run /dev/shm/my-dispatch-validation-new/run \
  --output /dev/shm/my-dispatch-validation-new/repeat-analysis
python experiments/paper_readiness/check_dispatch_network.py \
  --prepared /dev/shm/my-dispatch-validation-new --run /dev/shm/my-dispatch-validation-new/run \
  --analysis /dev/shm/my-dispatch-validation-new/analysis --repeat-analysis /dev/shm/my-dispatch-validation-new/repeat-analysis \
  --output /dev/shm/my-dispatch-validation-new/checked
python experiments/paper_readiness/analyze_dispatch_validation.py \
  --observed /dev/shm/my-dispatch-validation-new/observed --run /dev/shm/my-dispatch-validation-new/run \
  --analysis /dev/shm/my-dispatch-validation-new/analysis --output /dev/shm/my-dispatch-validation-new/metrics
python experiments/paper_readiness/check_dispatch_validation_metrics.py \
  --observed /dev/shm/my-dispatch-validation-new/observed --run /dev/shm/my-dispatch-validation-new/run \
  --analysis /dev/shm/my-dispatch-validation-new/analysis --metrics /dev/shm/my-dispatch-validation-new/metrics/metrics.json \
  --packet-check /dev/shm/my-dispatch-validation-new/checked/verification.json --output /dev/shm/my-dispatch-validation-new/review
```

最后一步生成独立核验、指标和HTML；正式报告的其他证据链接由检查点打包补齐。自定义复核目录可直接读取生成的两个JSON；不会自动备份，也不能把仅分析重复当作网络重复。分析器不读`truth/`，独立包核验才读取原始请求及Prefill放置。静态服务量计算代码在执行后实现，已逐出口与实际包发送量核对，只作基线检验，不是前瞻策略评测。

新增的千卡短任务用于判别宿主成本，执行前范围见[方法](host_scale_protocol.md)，实际结果见[HTML报告](../../docs/review_artifacts/host_scale_20261022/report.html)。固定平台`bc8dd6f`，单次1024GPU/256个EP4副本、人工1层×2轮Decode在同一网络完成；不替代旧48层模型、现代DeepEP或硬件校准。准备脚本不导入平台私有生成器，分析只读公开产物和稳定计时读取接口；路径/消息/回调再由独立脚本核验。

复现使用新的目录和已有前端依赖环境，`PYTHONPATH`设为本仓库`python`的绝对路径。以下各命令按方法中的时间/内存/磁盘预算分别监测，`rdma-run`内部也使用输入的显式资源限额；失败不自动重试。

```bash
python experiments/paper_readiness/host_scale.py prepare --output /dev/shm/my-host-scale-new
python -m moe_ub_cosim scaleout topology \
  --config /dev/shm/my-host-scale-new/topology.json --output /dev/shm/my-host-scale-new/topology
python -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/my-host-scale-new/run-monitor \
  --rss-bytes 6442450944 --address-bytes 12884901888 \
  --artifact-bytes 67108864 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/my-host-scale-new/run -- \
  python -m moe_ub_cosim scaleout rdma-run \
  --config /dev/shm/my-host-scale-new/service.json --topology /dev/shm/my-host-scale-new/topology \
  --protocol /dev/shm/my-host-scale-new/protocol.json --backend /absolute/path/to/service \
  --protoc /absolute/path/to/protoc --host-timing --output /dev/shm/my-host-scale-new/run
python experiments/paper_readiness/host_scale.py analyze \
  --run /dev/shm/my-host-scale-new/run --output /dev/shm/my-host-scale-new/analysis
python experiments/paper_readiness/check_host_scale.py \
  --run /dev/shm/my-host-scale-new/run --analysis /dev/shm/my-host-scale-new/analysis/analysis.json \
  --monitor /dev/shm/my-host-scale-new/run-monitor/probe.json --output /dev/shm/my-host-scale-new/review
```

最后的review页面同时生成`analysis.json`、`verification.json`和`maintenance.json`，可直接查看。正式报告目录另存完整流程命令、输入与逐hash核对的本机归档`archive.json`；自定义复核不自动创建备份。完整命令及退出状态见正式报告的维护记录；失败时只执行前缀分析，不执行要求完整结果的路径核验。

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

## Prefill放置的包级Dispatch数据实验

固定协议见[dispatch_network_protocol.md](dispatch_network_protocol.md)，结果与解释见[报告](../../docs/review_artifacts/dispatch_network_20261022/report.html)。这是EP4、层3、Decode位置1—4的六条件数据传输微基准，另固定重复一次；不执行计算、Combine、控制操作、自发GIN或现代完整库。下一轮依赖上一轮全部实际ACK。配置固定输入范围与放置SHA，不能直接改几个字段后仍把它当作同一研究；新研究须另行声明选择规则与范围。

需要四份既有原件及manifest、前阶段保存的placements.json，以及按固定依赖构建的原生stream_probe。公开构建方法见[持久字节入口](../../examples/scaleout_rdma_streams/README.md)，已有可用二进制无需重建。本轮复用 `/dev/shm/moe-fault-20261021/native/output/moe-rdma/ns3.42-moe-rdma-stream-probe`，其SHA、加载库和命令保存在结果的execution/verification中，没有安装、构建、联网或GPU执行。

从仓库根执行，先将 `COSIM_DISPATCH_BACKEND` 设置为已有二进制绝对路径。每命令60秒/RSS1GiB/地址2GiB/产物64MiB，整个六条件加重复运行器同样60秒；仿真5ms，卷预留8GiB。各阶段输出须不存在，失败保存且停止，不提高限额或重跑失败的网络；代码断言不可用 `python -O` 关闭。

```bash
COSIM_DISPATCH_BACKEND=/dev/shm/moe-fault-20261021/native/output/moe-rdma/ns3.42-moe-rdma-stream-probe
COSIM_DISPATCH_WORK=$(mktemp -d /dev/shm/moe-dispatch-network.XXXXXX)
dispatch_budget() {
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_DISPATCH_WORK/$1-probe" --artifact-root "$COSIM_DISPATCH_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
    --wall-seconds 60 --min-free-bytes 8589934592 -- python3 "${@:2}"
}
dispatch_budget prepare experiments/paper_readiness/prepare_dispatch_network.py \
  --manifest data/moe-trace-small/manifest.json \
  --placements docs/review_artifacts/prefill_placement_20261022/placements.json \
  --config experiments/paper_readiness/dispatch_network.json --source-root "$PWD" \
  --output "$COSIM_DISPATCH_WORK/prepared"
dispatch_budget run experiments/paper_readiness/run_dispatch_network.py \
  --observed "$COSIM_DISPATCH_WORK/prepared/observed" --backend "$COSIM_DISPATCH_BACKEND" \
  --output "$COSIM_DISPATCH_WORK/run"
dispatch_budget analyze experiments/paper_readiness/analyze_dispatch_network.py \
  --observed "$COSIM_DISPATCH_WORK/prepared/observed" --run "$COSIM_DISPATCH_WORK/run" \
  --output "$COSIM_DISPATCH_WORK/analysis"
dispatch_budget repeat-analyze experiments/paper_readiness/analyze_dispatch_network.py \
  --observed "$COSIM_DISPATCH_WORK/prepared/observed" --run "$COSIM_DISPATCH_WORK/run" \
  --output "$COSIM_DISPATCH_WORK/repeat-analysis"
dispatch_budget check experiments/paper_readiness/check_dispatch_network.py \
  --prepared "$COSIM_DISPATCH_WORK/prepared" --run "$COSIM_DISPATCH_WORK/run" \
  --analysis "$COSIM_DISPATCH_WORK/analysis" --repeat-analysis "$COSIM_DISPATCH_WORK/repeat-analysis" \
  --source-root "$PWD" --output "$COSIM_DISPATCH_WORK/verified"
```

输出中observed只含协议、物理配置和按目的rank去重的工作，truth保留原件身份和工作来源。分析只读取observed及公开结果，保存summary、rounds、packets.csv、ports.csv；独立检查回查原件、序号、所有数据及ACK路径、32位ECMP哈希、逐出口队列积分和回调因果。包的message_id为-1，关联使用被动源包记录、字节序号和实际端点，不向包头添加工作ID。队列积分不能当作可消除的业务损失。

只读打包命令如下；若检查器曾失败，另外以 `--probe` 逐项传入失败和更正后的probe，保留原因。报告文案对应固定研究事实，不用于替换输入后的自动论文结论。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_DISPATCH_WORK/review-probe" --artifact-root "$COSIM_DISPATCH_WORK" \
  --artifact-root docs/review_artifacts/my-dispatch-network --artifact-root runs/my-dispatch-checkpoint \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/build_dispatch_network_review.py \
  --prepared "$COSIM_DISPATCH_WORK/prepared" --run "$COSIM_DISPATCH_WORK/run" \
  --analysis "$COSIM_DISPATCH_WORK/analysis" --repeat-analysis "$COSIM_DISPATCH_WORK/repeat-analysis" \
  --verified "$COSIM_DISPATCH_WORK/verified" --source-root "$PWD" \
  --probe "$COSIM_DISPATCH_WORK/prepare-probe" --probe "$COSIM_DISPATCH_WORK/run-probe" \
  --probe "$COSIM_DISPATCH_WORK/analyze-probe" --probe "$COSIM_DISPATCH_WORK/repeat-analyze-probe" \
  --probe "$COSIM_DISPATCH_WORK/check-probe" \
  --output docs/review_artifacts/my-dispatch-network --checkpoint runs/my-dispatch-checkpoint
```

归档包含原件、输入、原始包/队列结果、分析、核验、精确命令和必要脚本，逐文件校验SHA；不包含自身清单、正在运行的打包probe或原生二进制。重建应使用锁定源码和公开rdma-build，不能假设其他机器的动态库二进制hash相同。原始产物和本轮默认128MiB累计预算由记录中的artifact_roots核算；全部生产样本、硬件准确性及千卡完整推理仍未验证。

## 固定分组后的节点映射强基线

设计见[mapping_baseline_protocol.md](mapping_baseline_protocol.md)，结论见[HTML报告](../../docs/review_artifacts/mapping_baseline_20261022/report.html)。固定三种分组，分别保留原编号、用Prefill最小化归一化远端副本、用Prefill最小化最大出口服务量，共9条件。静态模型固定upper25G/seed11；服务量不是完成时延。准备调用公开topology，规划只读observed，评价才读Decode；独立检查回查原件并与上一轮真实数据/ACK路径对照，不执行新网络。

本轮Hugging Face目录连接失败，原始响应0B，没有新请求留出组。失败collection.json和当时的collector-attempt.py均保存在检查点。初版urllib的20秒socket timeout实际允许40.07秒请求，已改为20秒子进程墙钟deadline，未重试远程采集；外层60秒监控在原失败中正常生效。不要把本轮采集失败说成原四份原始路由未取得。

复现数值不需要联网。以下使用本轮保留的失败collection记录；检查点展开后可将 `COSIM_MAPPING_HOLDOUT` 指向 `saved_run/holdout`，将参考路径指向 `reference_packet_result.json`。原四份JSON及manifest和固定placements须在指定source-root下可用。

```bash
COSIM_MAPPING_WORK=$(mktemp -d /dev/shm/moe-mapping-baseline.XXXXXX)
COSIM_MAPPING_HOLDOUT=/dev/shm/moe-mapping-holdout-20261022
COSIM_MAPPING_REFERENCE=/dev/shm/moe-dispatch-network-run-20261022/upper25g-prefill_all_lpt/result.json
mapping_budget() {
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_MAPPING_WORK/$1-probe" --artifact-root "$COSIM_MAPPING_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
    --wall-seconds 60 --min-free-bytes 8589934592 -- python3 "${@:2}"
}
mapping_budget prepare experiments/paper_readiness/mapping_baseline.py prepare \
  --manifest data/moe-trace-small/manifest.json --source-root "$PWD" \
  --placements docs/review_artifacts/prefill_placement_20261022/placements.json \
  --holdout "$COSIM_MAPPING_HOLDOUT" --output "$COSIM_MAPPING_WORK/prepared"
mapping_budget plan experiments/paper_readiness/mapping_baseline.py plan \
  --observed "$COSIM_MAPPING_WORK/prepared/observed" --output "$COSIM_MAPPING_WORK/planned"
mkdir "$COSIM_MAPPING_WORK/relocated-observed"
cp "$COSIM_MAPPING_WORK/prepared/observed/inputs.json" "$COSIM_MAPPING_WORK/relocated-observed/"
mapping_budget repeat-plan experiments/paper_readiness/mapping_baseline.py plan \
  --observed "$COSIM_MAPPING_WORK/relocated-observed" --output "$COSIM_MAPPING_WORK/repeat-planned"
mapping_budget evaluate experiments/paper_readiness/mapping_baseline.py evaluate \
  --prepared "$COSIM_MAPPING_WORK/prepared" --planned "$COSIM_MAPPING_WORK/planned" \
  --output "$COSIM_MAPPING_WORK/evaluated"
mapping_budget check experiments/paper_readiness/check_mapping_baseline.py \
  --prepared "$COSIM_MAPPING_WORK/prepared" --planned "$COSIM_MAPPING_WORK/planned" \
  --repeat-planned "$COSIM_MAPPING_WORK/repeat-planned" --evaluation "$COSIM_MAPPING_WORK/evaluated" \
  --reference-result "$COSIM_MAPPING_REFERENCE" --output "$COSIM_MAPPING_WORK/verified"
mapping_budget bound experiments/paper_readiness/mapping_baseline_bound.py \
  --prepared "$COSIM_MAPPING_WORK/prepared" --evaluation "$COSIM_MAPPING_WORK/evaluated" \
  --output "$COSIM_MAPPING_WORK/bound"
mapping_budget bound-check experiments/paper_readiness/check_mapping_bound.py \
  --prepared "$COSIM_MAPPING_WORK/prepared" --evaluation "$COSIM_MAPPING_WORK/evaluated" \
  --bound "$COSIM_MAPPING_WORK/bound" --output "$COSIM_MAPPING_WORK/bound-verified"
```

bound入口读取未来Decode，只供结果后的机制核查，不可用于部署规划。它计算K−1宽松下界和未来全知、每层固定排列的副本oracle；检查器以子集动态规划独立验证，不共享优化实现。不要将该oracle标为可实现收益或新算法。

只有需要另一个事先固定的新请求组时才运行采集入口：`collect_mapping_holdout.py --output /绝对路径/不存在的目录`，通过同一60秒/RSS/磁盘host_probe包裹；固定revision、首个目录响应的文件名/大小规则、4份/单份4MiB/总16MiB，不重试或按结果替换。本研究的命令和失败状态已保存，指标复现不要重新联网。采集脚本只处理公开JSON，不执行下载代码。

HTML与完整本地归档入口如下；八个stage分别保存输入、决策、评价、独立核查和失败资料。单阶段32MiB、宿主命令60秒/RSS1GiB/地址2GiB，共享新产物64MiB/累计128MiB，卷预留8GiB。失败保留停止，不自动扩预算。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_MAPPING_WORK/review-probe" --artifact-root "$COSIM_MAPPING_WORK" \
  --artifact-root docs/review_artifacts/my-mapping-baseline --artifact-root runs/my-mapping-checkpoint \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/build_mapping_review.py \
  --stage "prepared=$COSIM_MAPPING_WORK/prepared" --stage "planned=$COSIM_MAPPING_WORK/planned" \
  --stage "evaluation=$COSIM_MAPPING_WORK/evaluated" --stage "repeat_planned=$COSIM_MAPPING_WORK/repeat-planned" \
  --stage "verification=$COSIM_MAPPING_WORK/verified" --stage "bound=$COSIM_MAPPING_WORK/bound" \
  --stage "bound_verification=$COSIM_MAPPING_WORK/bound-verified" --stage "holdout=$COSIM_MAPPING_HOLDOUT" \
  --probe "$COSIM_MAPPING_WORK/prepare-probe" --probe "$COSIM_MAPPING_WORK/plan-probe" \
  --probe "$COSIM_MAPPING_WORK/repeat-plan-probe" --probe "$COSIM_MAPPING_WORK/evaluate-probe" \
  --probe "$COSIM_MAPPING_WORK/check-probe" --probe "$COSIM_MAPPING_WORK/bound-probe" \
  --probe "$COSIM_MAPPING_WORK/bound-check-probe" --reference-result "$COSIM_MAPPING_REFERENCE" \
  --output docs/review_artifacts/my-mapping-baseline --checkpoint runs/my-mapping-checkpoint
```

打包只读，不重跑研究。原始windows.csv、完整候选计划、逐层下界、原件及参考包记录在归档中逐文件核hash；Git保存九条件摘要和下界摘要。归档不含自身清单和正在运行的打包probe，打包资源单独记录。报告对应固定研究事实，不能把修改输入后的文件直接替换为已有结论。

## 分组路由约束与结论修正

本轮先回查旧8条件，再补cap4与within32各3种子，共14条件。前者只增加“每token最多4个原始32专家逻辑组”的必要条件；后者保持每token逻辑组计数，作为连续放置下通信不变的负对照。两者均保持每source/层专家边际次数，不是可执行模型推理。协议见[routing_constraint_protocol.md](routing_constraint_protocol.md)，实际结果与近邻MoETuner对比见[中文HTML](../../docs/review_artifacts/routing_constraints_20261022/report.html)。旧无约束交换约79%—80%超出参考组数限制，其+14.5%左右通信增幅不能推广；cap4为约−1%，within32完整矩阵不变。旧原始结果与归档保留。

复现不联网。先从校验过的本轮检查点恢复四原件到主仓库`data/moe-trace-small/`，恢复旧observed到自己的输入目录，按[archive.json](../../docs/review_artifacts/routing_constraints_20261022/archive.json)逐文件核hash。检查点成员`old_observed/`是旧公开输入，不是truth；`saved_run/prepared/observed/`是本轮六条件。`COSIM_ROUTING_OLD`应指向前者。脚本默认相对自身定位主仓库，换cwd不改变原件根目录。

以下在主仓库根执行。`COSIM_ROUTING_WORK`必须为不存在的目录，阶段输出不能覆盖旧目录。每命令60秒/RSS1GiB/地址2GiB；host_probe合并记账工作目录，新产物64MiB、累计128MiB、预留8GiB。准备在生成前核hash并检查原件；原件超限直接停止，不过滤样本或放松上限。

```bash
COSIM_ROUTING_WORK=/dev/shm/my-routing-constraint-study
COSIM_ROUTING_OLD=/绝对路径/已恢复的old_observed
mkdir "$COSIM_ROUTING_WORK"
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_ROUTING_WORK/prepare-probe" --artifact-root "$COSIM_ROUTING_WORK" \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/routing_constraints.py prepare \
  --manifest data/moe-trace-small/manifest.json --old-observed "$COSIM_ROUTING_OLD" \
  --output "$COSIM_ROUTING_WORK/prepared"
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_ROUTING_WORK/analyze-probe" --artifact-root "$COSIM_ROUTING_WORK" \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/routing_constraints.py analyze \
  --old-observed "$COSIM_ROUTING_OLD" --new-observed "$COSIM_ROUTING_WORK/prepared/observed" \
  --output "$COSIM_ROUTING_WORK/analyzed"
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_ROUTING_WORK/check-probe" --artifact-root "$COSIM_ROUTING_WORK" \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/check_routing_constraints.py \
  --manifest data/moe-trace-small/manifest.json --old-observed "$COSIM_ROUTING_OLD" \
  --prepared "$COSIM_ROUTING_WORK/prepared" --analysis "$COSIM_ROUTING_WORK/analyzed" \
  --placements docs/review_artifacts/prefill_placement_20261022/placements.json \
  --output "$COSIM_ROUTING_WORK/verified"
```

分析只读取observed。独立checker另读四原件与truth，逐窗口重算而不导入生成/分析脚本；还检查参考cap、within32逐token组计数与物理计数、边际热度、旧统计及LPT组跨度。可另用相同prepare参数生成`repeat-prepared`，再用checker的`--repeat-prepared`比较12份非执行日志产物；正式本轮已换cwd完成此核对。不要重复执行同一输出目录。

打包器要求上述四个probe和重复准备结果全部存在，并使用固定本轮结果与报告；它不是任意输入的通用结论生成器。`--work`下还需有`repeat-prepare-probe`，`--source-probe`指本轮采集资源记录。`--sources`和`--holdout-failure`分别恢复自本轮归档的`sources/`与`prior_failed_holdout/`；两份config恢复自`reference_configs/`。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
  --output-dir "$COSIM_ROUTING_WORK/review-probe" --artifact-root "$COSIM_ROUTING_WORK" \
  --artifact-root docs/review_artifacts/my-routing-review --artifact-root runs/my-routing-checkpoint \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 \
  -- python3 experiments/paper_readiness/build_routing_review.py \
  --work "$COSIM_ROUTING_WORK" --old-observed "$COSIM_ROUTING_OLD" \
  --sources /绝对路径/恢复的sources --source-probe /绝对路径/恢复的saved_run/source_probe \
  --holdout-failure /绝对路径/恢复的prior_failed_holdout \
  --reference-config /绝对路径/reference_configs/deepseek_v3_config.json \
  --reference-config /绝对路径/reference_configs/deepseek_R1_0528_config.json \
  --output docs/review_artifacts/my-routing-review --checkpoint runs/my-routing-checkpoint
```

来源采集器`collect_placement_sources.py --output 新目录`只用于新的明确资料采集，沿同一probe预算包裹；本轮已取得固定MoETuner v1，HF元数据超时0B，没有重试。复现指标不运行它。`collect_mapping_holdout.py`在本轮修正递归subject目录查询、名称排序与同名拒绝，但修正版未远端执行；原失败脚本及记录在检查点中，不宣称新请求已经取得。文献比较不是穷尽检索，MoETuner优化器尚未复现，旧本机config也不是历史采集模型身份的证明。

## 分层EPLB强基线与拓扑感知近邻

方法先见[eplb_group_protocol.md](eplb_group_protocol.md)，实际结果见[报告HTML](../../docs/review_artifacts/eplb_groups_20261022/report.html)。本轮对比五条件：连续、全/尾Prefill单专家计数LPT、全/尾Prefill组计数的分层EPLB思路适配。固定EP4/四节点各一个活动GPU、256物理专家、零额外副本；不执行官方torch代码，显式定义平局规则，不声称原版运行时复现。分层副本接近连续，但计算输入p95没有保住单专家LPT的改善；不是新方法收益或延迟结论。

指标复现不联网、不安装。按[归档清单](../../docs/review_artifacts/eplb_groups_20261022/archive.json)恢复四原件到`data/moe-trace-small/`，其余资料恢复到自己的输入目录；下例`COSIM_EPLB_INPUT`是恢复根，含`sources/code/`、`sources/commits/`、`sources/trees/`。创建新的工作目录，在主仓库根执行。已有准备与评价脚本通过CLI复用，平台没有新增功能或依赖。

```bash
COSIM_EPLB_WORK=/dev/shm/my-eplb-groups
COSIM_EPLB_INPUT=/绝对路径/已恢复的检查点根
mkdir "$COSIM_EPLB_WORK"
eplb_stage() {
  local eplb_stage_name=$1
  shift
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_EPLB_WORK/$eplb_stage_name-probe" --artifact-root "$COSIM_EPLB_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
    --wall-seconds 60 --min-free-bytes 8589934592 -- "$@"
}
eplb_stage prepare python3 experiments/paper_readiness/prepare_placement_baselines.py \
  --manifest data/moe-trace-small/manifest.json --output "$COSIM_EPLB_WORK/prepared"
eplb_stage plan python3 experiments/paper_readiness/plan_eplb_groups.py \
  --observed "$COSIM_EPLB_WORK/prepared/observed" \
  --reference-placements docs/review_artifacts/prefill_placement_20261022/placements.json \
  --reference-source "$COSIM_EPLB_INPUT/sources/code/eplb.py" --output "$COSIM_EPLB_WORK/planned"
eplb_stage evaluate python3 experiments/paper_readiness/evaluate_placement_baselines.py \
  --prepared "$COSIM_EPLB_WORK/prepared" --placements "$COSIM_EPLB_WORK/planned" \
  --output "$COSIM_EPLB_WORK/evaluated"
cp -r "$COSIM_EPLB_WORK/prepared/observed" "$COSIM_EPLB_WORK/relocated-observed"
eplb_stage repeat-plan python3 experiments/paper_readiness/plan_eplb_groups.py \
  --observed "$COSIM_EPLB_WORK/relocated-observed" \
  --reference-placements docs/review_artifacts/prefill_placement_20261022/placements.json \
  --reference-source "$COSIM_EPLB_INPUT/sources/code/eplb.py" --output "$COSIM_EPLB_WORK/repeat-planned"
eplb_stage check python3 experiments/paper_readiness/check_eplb_groups.py \
  --work "$COSIM_EPLB_WORK" --source-commits "$COSIM_EPLB_INPUT/sources/commits" \
  --source-trees "$COSIM_EPLB_INPUT/sources/trees" --source-code "$COSIM_EPLB_INPUT/sources/code" \
  --output "$COSIM_EPLB_WORK/verified"
```

每命令60秒/RSS1GiB/地址2GiB，单阶段32MiB、新产物64MiB/累计128MiB、预留8GiB，输出目录必须新建。准备将Decode与来源放truth；规划仅读observed和已固定的Prefill放置/公开源码身份，checker回查原件、逐决策最小负载、逐窗口矩阵、分布和配对。正式本轮的observed重复还换了cwd；该操作是离线规划，未重跑模型或网络。

`collect_topology_baselines.py`默认只读NeurIPS2022目录与ExFlow精确标题的arXiv元数据；`--source NAME=HTTPS_URL`用于从前一步实际取得的精确链接取文，单批最多3请求。各响应2MiB/20秒，整轮累计16MiB，由host_probe限60秒；不重试、换源、执行下载代码或初始化依赖。本轮TA-MoE正文/补充及固定gate、EPLB固定README/源码已取得，ExFlow PDF超大小上限未取得；复现指标不重新联网。

`build_eplb_review.py --work 工作目录 --source-group NAME=目录 --source-probe PROBE目录 --output 仓库内新报告目录 --checkpoint 仓库内新归档目录`生成固定本轮报告与逐文件hash检查点。`--source-group`必须提供`discovery/primary/paper/commits/trees/code`六项，source probe按六阶段分别传入。它要求prepare/plan/evaluate/repeat-plan/check五个probe与核验结果，单独用同样预算的host_probe包裹，并将报告/归档路径加入artifact-root。报告对应固定事实，不能用任意新输入自动生成原结论。

## 整组容量约束的精确空间

[协议](group_capacity_protocol.md)先限定可行域：8逻辑组两两配对、4rank各64专家、零冗余、每层放置在128位置内不变；[结果HTML](../../docs/review_artifacts/group_capacity_20261022/report.html)说明为何原Prefill计数目标已最优，而未来Decode oracle仍有有限空间。oracle明确读取truth和未来数据，不是可部署方法，不报告时延或论文收益。

原始四文件仍按manifest恢复到`data/moe-trace-small/`。参考分层放置可从已核验的上轮持久检查点中读取指定成员；下面所有路径均须新建，在仓库根执行。根卷不足8GiB预留时，不把大结果/归档写到根卷，保持相同预算，使用空间充足的/dev/shm。它是易失临时存储，不能称为持久备份。

```bash
COSIM_GROUP_WORK=/dev/shm/my-group-capacity
mkdir "$COSIM_GROUP_WORK"
tar -xOf runs/eplb_groups_checkpoint_20261022/checkpoint.tar.gz \
  saved_run/planned/placements.json > "$COSIM_GROUP_WORK/reference_placements.json"
group_stage() {
  local group_stage_name=$1
  shift
  PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m moe_ub_cosim.host_probe \
    --output-dir "$COSIM_GROUP_WORK/$group_stage_name-probe" --artifact-root "$COSIM_GROUP_WORK" \
    --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 67108864 \
    --wall-seconds 60 --min-free-bytes 8589934592 -- "$@"
}
group_stage prepare python3 experiments/paper_readiness/prepare_placement_baselines.py \
  --manifest data/moe-trace-small/manifest.json --output "$COSIM_GROUP_WORK/prepared"
group_stage enumerate python3 experiments/paper_readiness/group_capacity_oracle.py enumerate \
  --prepared "$COSIM_GROUP_WORK/prepared" --reference-placements "$COSIM_GROUP_WORK/reference_placements.json" \
  --output "$COSIM_GROUP_WORK/enumerated"
group_stage optimize python3 experiments/paper_readiness/group_capacity_oracle.py optimize \
  --candidates "$COSIM_GROUP_WORK/enumerated" --output "$COSIM_GROUP_WORK/optimized"
cp -r "$COSIM_GROUP_WORK/enumerated" "$COSIM_GROUP_WORK/relocated-candidates"
group_stage repeat-optimize python3 experiments/paper_readiness/group_capacity_oracle.py optimize \
  --candidates "$COSIM_GROUP_WORK/relocated-candidates" --output "$COSIM_GROUP_WORK/repeat-optimized"
group_stage check python3 experiments/paper_readiness/check_group_capacity.py \
  --work "$COSIM_GROUP_WORK" --repeat "$COSIM_GROUP_WORK/repeat-optimized" --output "$COSIM_GROUP_WORK/verified"
group_stage diagnostic python3 experiments/paper_readiness/analyze_group_witness.py \
  --work "$COSIM_GROUP_WORK" --output "$COSIM_GROUP_WORK/diagnostic"
```

每命令60秒/RSS1GiB/地址2GiB、单阶段32MiB、新产物64MiB/累计128MiB、预留8GiB；失败不抬限或重跑失败阶段。enumerate保存全部6090候选及Prefill最优值，optimize对69409副本预算/371超阈窗口求界。独立checker从四原件重算，使用不同配对枚举、指派DP与Pareto前沿；diagnostic只解释已选见证的Prefill代价与已有直方图，不增加优化条件。正式本轮仅迁移候选表、换cwd重复求界，oracle.json字节一致。

固定结果打包入口为`build_group_capacity_review.py --work 工作目录 --output 仓库内新报告目录 --checkpoint 工作目录内的新检查点目录`；需要上述六个成功probe和重复核验，外层同样用host_probe，报告和checkpoint加入artifact-root。打包器要求checkpoint在work内且位于/dev/shm以保持产物卷预留；Git清单明确记录`tmpfs_volatile`，不将其当成持久备份。它核hash后归档原件、全部候选、见证、证书与脚本，不重跑网络或GPU。
# 千卡失败产物的有限阶段审计（20261022）

平台持久索引与计数增量`5264d11`、控制日志按行写出修复`0d8ee06`分别验收，不属于本研究的新网络执行。旧1024GPU实验为256个EP4副本、两轮ready-KV Decode；不是EP1024或训练DP。

本次全量日志分析触及32MiB输出上限，失败保留。最终只交付baseline已写摘要的恢复与两案例stdout/尾部的独立核查；numeric全量计数缺失。发现历史worker块缓冲让保存的通知数落后于节点进度；未来层RECV可提前投递，issue层号也不是实际计算层进度。详细结果、失败和下一步判别见[HTML报告](../../docs/review_artifacts/scale1024_audit_20261022/report.html)与[方法/预算](scale1024_audit_protocol.md)。完整分析器已限制输出样本，但修改后未重跑全量，不标为验收通过。

```bash
python3 experiments/paper_readiness/check_scale1024_audit.py \
  --summary /absolute/checkpoint/summary/summary.json \
  --output /absolute/new-verification.json
python3 experiments/paper_readiness/build_scale1024_review.py \
  --summary-directory /absolute/checkpoint/summary \
  --output /absolute/new-report-directory
```

检查器读取原有两个stdout与各1MiB节点尾部，并核对已固定的历史Git源码；原始目录不可用时不能仅靠摘要冒充原件复核。报告生成只读小型已核验产物。运行仍须使用协议声明的时间/内存/磁盘监控，不自动重跑千卡失败网络。
