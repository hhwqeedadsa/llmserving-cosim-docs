# 有限记录存储与跨轮释放

这是 M10 资源工程中已实现的存储接口，不改变业务消息、依赖、完成时刻或研究观测的含义。本接口的局部通过不是最终目标场景验收。

## 入口和文件版本

服务可显式传入 `--record-storage POLICY.json`，沿用原配置、系统、网络及观测参数。策略示例为：

```json
{
  "schema_version": "moe-ub-record-storage/1",
  "max_compressed_bytes": 268435456,
  "max_buffer_bytes": 1048576,
  "max_record_bytes": 262144
}
```

这是小型回归用预算，不是完整目标预算。前台把策略保存为运行目录的 `record-storage.json`；后端从共享目录读取，拒绝未知字段、非正整数和无法容纳一条记录与 64 KiB 压缩块的缓冲配置。不传此选项时保留原 `result/1` 数组和原端口文件。

新模式输出 `moe-ub-service-result/2`。大数组通过 `record_storage.fields` 引用 `execution/records` 中的 gzip JSONL 文件，包含记录数、原始字节、压缩字节和原始内容 CRC32。每个文件可以有多个标准 gzip member；每个 member 自带 gzip 校验。按图保存的文件在结果中按提交顺序排列，因此多个组完成顺序不同时仍还原原数组顺序。WQE 和目标写事件按实际回调顺序记录。

`RecordStore` 共享有限的缓冲区；达到块大小或总缓冲上限时压缩并关闭文件，不为每个端口常驻文件句柄和压缩器。压缩内容写入前计入磁盘限额；错误使后续写入失败，不能产生成功结果。该限额只涵盖记录文件内容，运行目录中的图、原生诊断日志、清单及文件系统分配空间仍需外层总预算监测。缓冲统计表示有效载荷字节，不冒充进程 RSS。

Python `record_storage.iter_result(execution, field)` 逐条读取新旧结果；`iter_port_records(execution, port, name)` 还原原端口逻辑记录。读取时检查文件大小、gzip 完整性、记录数、字节数、CRC32和单条大小；调用方必须消费完迭代器才能完成末尾核验。CRC32 是损坏检测，不是来源认证；原始输入摘要及来源核验另行保留。

## 执行记录与释放条件

原 ASTRA 日志保留 10 MiB 的当前文件及十个轮转文件，大图可能超过此容量。新模式额外保存 workload 的原始 issue/callback 消息；原诊断日志不变。每图完成后等待异步日志队列落盘，封存当前压缩块，逐条读入活动图的执行时刻。检查每个节点的 issue/callback 唯一性、计算时长及本地依赖，拒绝未知或已经释放图的事件。

在原 ns-3 完成事件之后，服务保存节点、端点、操作、传输和本地交付结果，确认发送/接收回调完成、Jetty 释放且没有挂起回调，再删除适配器中的借用指针和图/传输对象。没有重启网络、追加通信等待或改动前台接纳时间。运行级编号用合并区间保留精确重复检查，编号空隙和乱序仍受检查。

包来源不能在事务完成时立即删除。服务先登记已完成图，等共享物理网络的在途包、端口、队列、TP 活动段及未确认段全部清空后才释放其包/描述符记录。其他活动图的记录保留。持续重叠且没有全局空闲的场景仍受明确来源记录上限约束，超限失败；小场景的峰值不能保证任意负载的峰值。

## 端口记录与空窗口

各端口的 `record-storage.json` 使用 `moe-ub-port-records/1`；字段引用相对于执行目录的同一个 records 目录。每个端口至多保留一个当前窗口，时间推进时把非零窗口写出，结束时封存最后一个窗口。`observed/public_config.json` 中的起止时刻、周期及清单中的逻辑窗口数精确表示所有空窗口，包括最后一个不足周期的窗口。读取器可还原原来逐窗口的全部行，拒绝重复、乱序或越界的非零窗口。

端点/操作/传输登记表通过同一份 `registry-records.json` 引用结果中的字段；各端口仅有清单硬链接，不复制整份表。来源关闭时端口不输出来源字段引用。原始队列、逐包时刻、业务/元数据/填充字节划分均保留，新的存储文件不直接充当研究公共观测。

空 gzip 记录只在封存时建立，之后禁止追加；相同运行中每组最多 32000 份空记录共享同一 inode，以避开文件系统链接数上限。各逻辑文件仍逐份计入压缩预算。完整目标零业务初始化的 80644 份记录由约 315 MiB 实际分配降到 12 KiB，全部后端结果和 96768 份端口元数据与原输出相同。

## 有限研究导出

`PYTHONPATH=python P -m moe_ub_cosim.bounded_export --raw RUN --observation OBS.json --policy POLICY.json --max-simulation-ns N --output-dir NEW` 用于已正常结束的 result/2 服务。P 为项目 Python 环境，RUN 包含 deployment、前台结果和 execution。策略版本为 `moe-ub-research-export-policy/1`，要求正整数 `max_index_bytes`、`max_compressed_bytes`、`max_record_bytes`、`max_endpoint_records`、`max_operations`、`max_layouts`、`max_id_ranges`、`max_graphs`、`cache_rows`。单条原始记录最多 64 MiB。索引和压缩记录分别限额，整个运行及导出目录仍须由外层监测总分配空间。

输出按 observed、truth、metrics 分目录，版本为 `moe-ub-research-storage/1`。每个目录的 storage.json 声明表及 gzip 文件、行数、字节数和 CRC32；所有压缩表均为 JSONL，包括逻辑名以 `.csv` 结尾的窗口表。`research_storage.stored_rows(directory, table)` 逐行读取。公共观测只含原白名单字段，时钟变换和来源保存在 truth；`validate_observed_storage(observed_directory)` 仅访问公共目录。

端口只存非零窗口，公共配置明确声明全部窗口边界，`research_storage.logical_windows(observed_directory)` 可逐行恢复完整窗口，包括空窗口及最后的不完整周期。字节分类同样只存非零窗口，`metrics/validation.json` 明示编码及是否存在填充字段；缺省窗口各分类均为零。这样不会为数十亿空窗口生成 CSV 行。

导出逐条比对计划、实际交付和来源，独立检查目标写入累计字节；每个网络传输临时占用 32 B 的定长磁盘索引。mmap 页面计入宿主 RSS，有限缓存只保留近期访问行，操作与布局表分别有上限。端点记录需要排序，受显式数量上限约束。最终包字节、窗口、来源和交付守恒全部通过后，先保存索引清理摘要，再删除该可再生索引；失败保留原因且不写成功 validation.json。压缩限额包括 gzip 头尾，不包括目录分配块及清单。

已有五类实际 UB 记录与旧导出算法逐字段对照通过，包括本地时钟偏移/漂移、来源关闭、8/32 轮；损坏目标写入校验拒绝。旧导出入口及输出保持不变，不能把旧入口当作新存储格式的完整规模入口。完整目标后处理的运行和磁盘预算仍需结合目标测量确定。

## 当前验证边界

前台完整模型服务现可显式传入 `--graph-storage POLICY.json`，同时必须使用 `--record-storage`。策略为 `schema_version="moe-ub-graph-storage/1"`，包含正整数 `max_recipe_bytes`（单图原始构图输入）、`max_archive_bytes`（累计归档）和 `max_active_graph_bytes`（活动图）。小型测试分别使用 8/64/64 MiB，不能照搬为目标预算。文件/目录占用按内容与实际分配较大值检查；归档和活动图预算在构图前后及释放后核对，运行中仍需宿主总监测限制峰值。

每轮保存压缩的配置、真实来源记录、模型/费用和运行编号状态，然后由短生命周期 Python 子进程完成构图、原完整校验及紧凑写出。子进程退出并核对摘要后才提交图给后端；前台只回收编号和路由消费状态，不把图留在自己的堆中。后端发出已封存记录的 WAVE_DONE 后，前台复核自己生成的全部文件，先写 `graph-replay/gN/cleanup.json`，再删除这些可再生文件。存在额外文件、文件内容变化或归档损坏时拒绝清理；旧任务和未知路径不受影响。

同一显式选项也把启动前的完整占位图组合校验放入独立进程，保留原完整构图/重构检查；其压缩输入、命令、错误及成功摘要保存在 `graph-replay/input-checks/组名`。这避免已丢弃占位图的 Python 分配空间长期留在服务进程中。没有使用此选项的旧入口继续在原进程校验。

重建命令为 `PYTHONPATH=python P -m moe_ub_cosim.graph_storage replay --archive RUN/graph-replay/gN/archive.json --output-dir NEW_DIRECTORY`，其中 P 为原 Python 环境。重建要求使用记录的源码及固定 proto 修订，重新执行完整校验，并核对 plan、Chakra、预算和 generation 摘要及编号结果。构图工具的诊断文件路径和 Python 缓存不是跨目录字节等价对象，但原文件也逐项登记后才清理。

前台五类真实 UB 对照及 8/32 轮压力已经通过，所有计划紧凑摘要、Chakra 摘要、请求与后端记录相同；结束无活动图。归档原始输入允许重建，不代替仿真产生的记录或研究导出。证据见 [checkpoint-graph.json](../fixtures/m10_resource_efficiency/checkpoint-graph.json)。完整服务输入验证的效率、研究导出及目标组合资源预检仍在进行。

已验证五类小型真实 UB 运行与旧结果及全部逻辑端口记录一致；8/32 轮小模型的图、传输和包来源峰值保持，正常结束清零。日志轮转、gzip 损坏、编号重复、活动对象拒绝释放、晚到匹配、窗口边界和磁盘超限有专项检查。最终结果、实际命令及失败修正记录归入 [资源工程记录](../fixtures/m10_resource_efficiency/README.md)。不能将现有旧导出命令直接当作新存储格式的完整规模入口。

## 激活前的记录归档与资源诊断

开启 record-storage 后，每张图通过全部加载和身份检查，再写出并封存 planned_transfers，完整读取核对记录数、原始字节与 CRC。之后可释放重复的传输 JSON，只留下端点计算所需的数值节点引用；实际执行对象和来源账本持有自己的已验证描述符。安装、来源登记完成后，原描述符数组释放。默认旧结果格式不使用这一释放路径，输入计划和 Chakra 不变。

本地环境变量 `MOE_UB_HOST_PREPARATION_TRACE=1` 使生产服务记录主机准备阶段时间和激活前资源快照，不改变业务激活/完成流程。独立 `cosim-service-resource-probe` 可执行文件在相同准备路径之后、激活之前终止，只用于共同驻留诊断，不生成正常 result.json，也不作为正常结束证明。二者通过小场景对照验证；目标规模测量的最终状态以对应探针记录为准。

## 原生辅助诊断的显式选择

UB 的 task/packet/port 文本诊断与本平台研究记录分别控制。完整目标首轮试测发现原生辅助日志在约 5.2% 写入完成时已增长到 0.985 GB。资源探针新增 `--native-diagnostics off`，通过既有 backend globals 显式关闭 `UB_TASK_TRACE_ENABLE`、`UB_PACKET_TRACE_ENABLE`、`UB_PORT_TRACE_ENABLE`，并记录参数来源；默认 keep 保留原行为。研究端点、逐包成功发送、队列、来源、目标写入和 ASTRA 原始执行记录均继续记录。生产服务使用相同的既有 backend.json 参数，未增加隐藏环境覆盖。

`--record-buffer-bytes` 只选择已有压缩存储缓冲上限，默认 1 MiB。四组真实 UB 对照验证旧/新空闲扫描、辅助日志开/关以及 1/64 MiB 缓冲下的协议、业务执行和全部研究记录相同。小场景没有填满 1 MiB，因此该对照只证明等价性，目标规模的压缩改善以复测为准。关闭辅助日志不删除已产生的诊断；失败探针的日志经逐文件 SHA-256 核验无损归档后才回收原文件。

## 下一轮构图前的宿主内存交接

图完成归档、删除执行对象后，局部执行时刻表也必须先销毁，再向前台发送 WAVE_DONE。当前原生 Linux/glibc 构建在这一边界调用 `malloc_trim(0)`，请求分配器归还已经空闲的页；来源账本在真实网络排空后删除时同样处理。只归还空闲页，不提前释放仍需等待的来源或网络对象；默认旧数组结果仍保留原生命周期。

完整首轮资源驱动器在收到 WAVE_DONE、发送下一次 COMMIT 之前读取后台 VmRSS/VmHWM，保存 wave-boundary-memory.json。这用于核对空闲后台、真实前台和独立下一轮构图的组合资源，不能用图对象计数归零代替 RSS。探针新增显式 `--record-bytes`、`--backend-wall-seconds`，分别设置压缩记录和后端协议等待上限，仍受外层累计磁盘及时间上限约束。

## 已退役 Jetty 的序号历史

有界服务在完成图之后、物理网络排空且所有参与 Jetty 已销毁时，回收接收侧按 Jetty 保存的 TA 序号参考。仍有 Jetty、部分接收、待发送/待确认片段或控制包时不回收；TP 通道对象、TP/PSN 累计序号及回绕参考保留。该入口用于每任务新建/销毁 Jetty 的 WRITE/RTP 服务，不适用于继续复用旧 Jetty 的原生应用。它不是网络重启或图完成的替代条件。

设置 `MOE_UB_HOST_PREPARATION_TRACE=1` 时，交接记录包含 glibc 堆的已分配/空闲字节，另保存 `transport-retirement.jsonl`。8/32 轮序号历史峰值同为 118 条、每轮清零；四块/128 rank/完整网络的 129034 条历史回收后，完整执行和研究记录逐字节一致。相关 UB 补丁为 `a9539dd`，固定基线未升级。

服务 `--wall-timeout-seconds` 接受显式正整数，默认仍为 60；宿主等待不计入仿真时间。完整首轮已实测超过一小时，因此不再把旧 3600 s 检查误当成技术上限。实验必须同时配置外层总时间、采样 RSS、地址空间及累计磁盘限额。原 [64 步预算](../fixtures/m10_resource_efficiency/final-run-budget.json)未执行，2026-10-05 已随用户的范围调整移出 M10 收尾；首轮研究交付的候选限额见[当前任务](tasks/current.md)。

现有图输入归档的 `graph_storage replay` 会重新构图。用户新要求的“直接复用已生成图运行后端”以及通用离线切片采样入口尚未交付，按 [v0.3 §4.9](moe_ub_cosim_dev_v0.3.md#49-首轮研究切片与重复实验)另行验收；不能把上述重建命令称为免构图的后端重放。
