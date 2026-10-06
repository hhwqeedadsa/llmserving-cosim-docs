# 观测记录与存储格式

本文件定义研究输出、原始执行记录、时间与字节口径，以及读取和资源预算。运行、采样与重放的操作步骤见[用户指南](user_guide.md)；完成事件和对象回收见[后端参考](../backend/README.md)。压缩与归档只改变保存方式，不改变业务消息、依赖和完成时刻。

## 哪些文件可以交给研究算法

`moe-ub run`、`sample` 和 `replay` 的研究结果采用相同的目录分工。算法仅以 `observed/` 为输入，其余目录用于复现、生成或评价。

| 目录 | 内容与用途 |
|---|---|
| `observed/` | 每 rank 的端侧操作、本地端口采样窗口和公开静态配置；不含全局操作号、传输号、请求身份、图节点、逐包来源或时钟变换参数 |
| `truth/` | 操作与端侧记录、传输、业务字节窗口的实际对应，以及内部时钟模型，供评价关联结果 |
| `metrics/` | 字节分类、守恒校验、存储和导出检查结果 |
| `inputs/`、`raw/`、`logs/` | 本次输入及摘要、执行计划与原始事件、运行资源和错误；是复现与排错资料，不属于算法输入 |

`sample` 复用的数据可能来自首轮数据包中的归档和工作缓存，未必在输出目录复制一整份 `raw/`。首轮数据包本身也含内部资料；共享算法输入时应选择其导出的 `observed/`，而不是直接传入整个数据包。[系统架构](architecture.md)说明这些记录如何从实际执行生成。

运行清单的 `coverage` 使用 `moe-ub-execution-coverage/1`，分别保存 `execution_complete`、`execution_budget_ns`、`simulation_end_ns`、`observation_complete` 和 `incomplete_ports`。观测完整指所选端口的本地半开窗口覆盖从时间零到实际完成时刻（包含该时刻的事件），不表示观测了全网。窗口之外没有记录，也不补零。此检查使用内部时钟参数，保存在执行资料中，不加入 `observed/`。原始后端另外记录 `execution_budget_ns` 和全局窗口的 `observation_coverage`；公开导出采用用户本地窗口重新计算覆盖，不能直接沿用后端采集范围。

## 公开表与字段

原有两表观测采用 `moe-ub-research-storage/1`，新增操作阶段与队列观测采用 `/2`。每个输出范围有自己的 `storage.json`，声明逻辑表名、文件名、行数、原始与压缩字节、原始内容 CRC32，以及单条记录上限。实际表放在该目录的 `records/` 下。

| 逻辑表 | 字段 |
|---|---|
| `endpoint_ops.jsonl` | `rank_id`、`local_record_id`、`kind`、`start_local_ns`、`end_local_ns` |
| `port_windows.csv` | `switch_id`、`port_id`、`direction`、`local_window_start_ns`、`local_window_end_ns`、`counted_packets`、`counted_packet_bytes` |

`local_record_id` 只在一个 rank 内编号，不编码全局操作身份。端侧起止时间取实际节点与回调，并按通信模板确定 Dispatch/Combine 的操作范围；不能用首包或末包时刻代替。`direction` 当前为 `egress`，包数与字节数为非负整数，时间单位为纳秒。

`public_config.json` 使用 `moe-ub-observed/1` 或带显式数量限制的 `/2`，声明 rank、观测端口、本地时间范围、周期、时钟分辨率、计数位置和字节口径；不包含时钟偏移与漂移。旧版 `observed/` 的压缩格式只允许 `public_config.json`、`storage.json`、`records/` 及上述两张表；下文 `/3` 公开配置额外允许阶段与队列表。校验器不会为了验证它们访问 `truth/`。

**逻辑名以 `.csv` 结尾的压缩表，内部也是 gzip JSONL。** 例如 `port_windows.csv.gz` 解压后每行一个 JSON 对象，应按清单和项目读取器解释，不能根据后缀交给 CSV 解析器。早期未压缩研究输出则直接保存 `endpoint_ops.jsonl` 和真正的 `port_windows.csv`；二者的字段与统计含义相同，文件编码不同。

## 计数位置与字节口径

### v0.4 操作阶段与等待统计

新增统计采用独立格式：输入 `moe-ub-observation-config/2` 的 `statistics` 为
`{"schema_version":"moe-ub-observation-statistics/1","queue_waiting_bytes":true,"operation_stages":true}`，
并显式给出观测资源预算；其余时钟、端口和来源字段沿用原格式。公开配置升级为
`moe-ub-observed/3`，压缩观测存储为 `moe-ub-research-storage/2`，增加以下两张表。
当前交付检查状态见[任务记录](tasks/current.md)，此处规定字段解释。

`endpoint_stages.jsonl` 每行只有 `rank_id`、`local_record_id`、`stage`、`time_local_ns`、`event_count`。
记录仍使用本 rank 的局部编号，时间转换使用该 rank 的本地时钟。阶段含义如下：

| stage | 实际来源与计数 |
|---|---|
| `prepare_start/end` | Dispatch 的 prepare 或 Combine 的 pack/local_merge 计算节点；每项计数 1，无实际节点则省略 |
| `local_prepare_start/end` | 模板为同芯片交付设置的本地准备节点；与上行准备分别计数 |
| `request_submit_first/last` | 本 rank 发起、属于该操作的逻辑 UB 任务的最早/最晚提交；计数为任务数，不是分段包数 |
| `data_ready` | 该 rank 作为消费者收到的全部应用消息的最晚数据就绪回调；计数包括业务、应用状态和同芯片交付，LOAD 取响应消费方 |
| `initiator_resources_complete` | 本 rank 发起的逻辑 UB 任务的最晚原生事务完成；计数与提交数相同，不表示 LOAD 供数方的全部缓冲状态 |
| `local_publish_first/last`、`local_resources_complete` | 同芯片交付的实际发布与发送回调，计数为交付数，不伪造 UB 请求 |
| `operation_complete` | 原 `endpoint_ops` 定义的操作终点，取真实依赖执行，计数 1 |

没有对应事件则省略该阶段，不补虚构时间。准备可以早于旧端侧操作区间的开始；各类就绪与资源完成不相互替代。
这些是仿真器可见的节点与回调，实际硬件是否能采集同样信息尚未确认。

`queue_windows.csv` 前五项端口与窗口字段沿用 `port_windows.csv`，分别增加
`voq_waiting_byte_ns/mean_bytes/peak_bytes` 和 `fifo_waiting_byte_ns/mean_bytes/peak_bytes`。
面积为精确非负整数 byte·ns，均值为面积除以本地窗口实际长度，峰值为整数 byte。
后端原始面积以十进制字符串保存，公开 Python 读取结果为整数，避免 64 位乘积溢出。

| 对象 | 包含范围 |
|---|---|
| `voq_waiting` | 已完成入口处理、等待指定出口调度的 VOQ 包；包含本地生成并进入 VOQ 的信用控制包，排除 ingress processing |
| `fifo_waiting` | 已进入该出口 FIFO、尚未取出发送的包；排除正在链路上发送的包，直接发送而未入 FIFO 的控制包不计入 |

两种对象均按队列实际保存的 `Packet::GetSize()` 计字节，包含该位置已有的头部及控制消息，不等于业务有效字节。
每个入队、转移、出队事件更新被动计数；统计不调用调度函数、不插入仿真事件。
窗口为本地半开区间，面积累计状态持续时间；峰值包含窗口内每次变化后的状态，包括同一时刻持续零纳秒的中间状态。
左边界前遗留的状态只在窗口内继续保持时计入；恰在左边界出队且立刻变零的旧状态不冒充窗口内峰值。
持续积压跨窗拆分，末窗用实际长度。发送计数表和队列表分别稀疏保存：无发送但等待量非零的窗口仍必须有队列记录。
`logical_queue_windows()` 按队列清单恢复其声明范围内的零窗口，不能从发送表的缺行推断等待量为零。

后端 `queue-state-events.jsonl` 保存时间零初值及每次变化后的两个等待量；`queue-windows.jsonl` 直接累计本次声明窗口。
当前详细采集的再次采样独立重算状态积分，并在原窗口配置下逐项核对直接累计结果。只有发送事件、没有这些状态资料的旧记录不能恢复队列统计。

交换出口成功开始发送时，按当时的 `Packet::GetSize()` 将整包计入窗口。一次包发送全部计入开始时刻所在窗口，不按包在链路上占用的时间拆分到多个窗口。发送尝试、入队和发送结束不是本计数位置；未包含在 ns-3 包中的物理编码或 FEC 字节也不计入。

| 字节类别 | 含义 |
|---|---|
| `business_payload_bytes` | 模板传输的有效激活或专家结果等业务范围；传统 `attribution_windows` 表采用这一口径 |
| `application_metadata_bytes` | 量化比例、索引、计数或状态标志等应用数据 |
| `padding_bytes` | 模板布局填充及原生 LOAD 短尾响应中超出有效范围的体字节；内存窗口预留空间不自动计入 |
| `data_protocol_overhead_bytes` | 承载上述数据的包中，剩余协议头等字节 |
| `control_packet_bytes` | TA/TP ACK、信用等协议控制包的整包字节 |
| `unattributed_bytes` | 分类输出保留的未归类计数字段；当前有效执行不以它掩盖缺失的 WRITE 来源 |

应用状态 WRITE 可以只含元数据，仍是应用消息，不能因为没有业务向量就归入协议控制包。TA/TP ACK 即使带任务标签，其载体字节也不获得业务操作归属。旧模板没有应用元数据或填充时相应量为零；`metrics/validation.json` 声明本次是否输出填充字段。

原生 STORE 的业务范围来自请求体，LOAD 来自响应体；读取请求及原生 ACK 计入控制包，响应头计入数据协议开销。32 B 供数就绪通知属于应用元数据。原生 `transfer_registry` 另含 `operation`、`initiator_rank`、`data_owner_rank`、`consumer_rank`；`src_rank/dst_rank` 表示请求方向。`native_starts`、`native_completions` 分别保存实际内存任务的开始与完成，不能用空的 WQE 记录推断原生任务未执行。

每个包每次经过一个观测出口都计数。跨服务器路径可能经过多个交换出口，因此全网端口字节之和不是唯一交付业务量。唯一交付量从目标有效写入核对；逐端口计数与原生端口累计值核对；每窗分类之和与整包计数核对；来源开启时，该窗各操作归属业务字节之和再与业务分类核对。来源关闭会停用操作归属检查并记录 `attribution_status="disabled"`，不改变原有执行和整包观测。

`truth/attribution_windows.csv` 保存 `operation_id`、端口与方向、本地窗口起止和 `business_payload_bytes`。这里的字节量表示该操作在该端口、该本地窗口中实际经过的业务字节，量大于零才表示二者有真实关联。对应关系从本次包的任务与字节范围产生，不从端侧时间相近或端口流量形状反推。

## 持续聚合与选择性详细记录

输入 `moe-ub-observation-config/3` 在 `/2` 的统计和预算字段外增加 `recording`：

```json
{
  "schema_version": "moe-ub-observation-recording/1",
  "mode": "aggregate",
  "detail_ranges": [{"switch_id": 4, "port_id": 4, "start_ns": 2000, "end_ns": 36000}],
  "truth_categories": ["business_data", "attached_metadata", "dispatch_count", "combine_flag", "load_ready"],
  "max_detail_events_per_port": 10000,
  "max_window_attribution_entries": 1000
}
```

`aggregate` 模式直接在当前事件更新包数、各类字节、队列面积与峰值，仅在窗口结束时序列化汇总；不先生成全部逐包 JSON。`detail_ranges` 为空表示只存窗口聚合，非空则额外保存所选出口和全局半开区间内的详细资料。每个所选端口最多给一个详细区间，必须是本次观测端口；总数受观测端口预算限制。`detailed` 模式为所有本次观测端口保存 `[0,max_simulation_ns)` 的详细资料，此时 `detail_ranges` 必须为空。两种模式都保存相同的基础窗口汇总和端侧阶段。

详细范围使用内部全局时间，保存在输入和执行资料，不写入公开观测。详细事件数包括发包、FIFO 审计、状态快照/变化及分类来源；同时受原事件字节预算和压缩记录总预算约束。`max_window_attribution_entries` 限制单端口当前窗口中的“操作、消息类别”条目数，达到上限报错，不丢弃记录。合并窗口导出使用独立导出策略的操作总数限制。操作与协议必要的在途状态始终存在，关闭详细保存不会关闭协议状态。

应用消息分类仅用于 `truth/` 的评价记录：

| `message_category` | 来源 |
|---|---|
| `business_data` | 激活、专家结果或 KV 的业务字节区域 |
| `attached_metadata` | 数据消息内实际复制的 scale、rank/token/topk 等元数据区域 |
| `dispatch_count` | CANN Dispatch 的计数发布，包括零贡献状态 |
| `combine_flag` | CANN Combine 的完成标志 |
| `load_ready` | 原生 LOAD 前真实发送的供数就绪通知 |

`truth_categories` 显式选择保留类别，关闭 `record_provenance` 时必须为空。`truth/message_attribution_windows.csv` 每行包含端口、方向、本地窗口起止、`operation_id`、`message_category` 和 `application_bytes`；其格式与选择声明在 `truth/message_categories.json`。零业务状态消息的归属来自实际传输的消息角色和字节区域，ACK、信用与响应头不会取得这些应用类别。传统 `attribution_windows.csv` 仍只保存所选 `business_data` 的投影，不改变原业务关联定义。保留部分元数据类别时，不宣称其和等于全部应用元数据。

后端采用 `moe-ub-port-records/3`，基础 `aggregate-windows.jsonl` 同时保存计数与字节分类，`queue-windows.jsonl` 保存队列面积与峰值，`truth/message-windows.jsonl` 保存所选类别的操作归属。只有明确选择详细区间的端口才有 `packet-events.jsonl`、`egress-queue-audit.jsonl`、`queue-state-events.jsonl` 和可选 `truth/classified_packet_ranges.jsonl`。清单声明具体范围及事件数/字节预算，不把汇总称为完整逐包来源。

新版队列详细历史用 `event_kind=initial/update/final` 区分区间初值、实际变化和结束快照。初值是左极限状态，不制造零时长峰值；结束快照关闭最后一段持续时间，队列在该详细区间结束时可以仍有积压。快照在下一个实际事件或最终导出时补记已知状态，不插入仿真事件；只有实际执行结束且网络排空后，才允许将后续声明区间记录为零状态。

`sample` 在创建输出目录和展开归档前检查能力：只有基础窗口时，要求交换机时钟保持、起止边界对齐、周期为原周期的整数倍，窗口范围不超出保存范围。包数和面积求和、峰值取最大、均值用合并后的实际长度重算。移动边界、细分或改变交换机时钟，要求选定端口有覆盖整个请求区间的发包与队列状态资料；按整数时钟反求边界，避免忽略映射到同一本地时刻的先前事件。端侧完整区间和阶段仍由已保存的实际时刻转换。

`slice inspect` 的 `capture_capabilities` 显示保存类型、基础窗口、详细范围、队列状态和真值类别。带新策略的数据包使用 `moe-ub-research-slice/2` 并校验能力声明；旧 `/1` 数据按原详细保存解释，采样范围和时钟能力保持，不能新增未采集的队列统计或消息类别。采样保持原捕获策略及类别选择；需要改变它们时重新执行 `run` 或 `replay`。各次输出只对自己 `public_config.ports` 声明的本地范围有效。

## 本地时钟、窗口与零值

执行只使用 ns-3 的全局时钟，被动统计和导出将其转换为设备本地时钟：

```text
local_ns = floor((1000000000 + drift_ppb) * global_ns / 1000000000) + offset_ns
```

`offset_ns` 为有符号纳秒，`drift_ppb` 为十亿分之一的漂移比例，整数计算向下取整。每个 rank 和每个被观测交换机恰有一份时钟，同一交换机的端口共享该时钟；校验正时钟比例和整数范围，保证转换结果单调不递减且不溢出。内部时钟与全局时刻保存在 `truth/clock_models.json` 和对应记录中，不写入公开表，也不改变发包或计算调度。

端口窗口使用本地半开区间 `[start, end)`。落在右边界的发送属于下一窗口；结束位置不足一个周期时仍保留较短的最后窗口。端侧记录保存操作的完整起止时刻，跨越端口观测边界的操作不会被裁剪成另一条操作。

压缩输出只写有包的窗口，`storage.json` 中的 `window_encoding="nonzero_with_declared_zero_windows"` 和 `logical_windows`，结合公开端口范围与周期，声明其余窗口均为零。清单覆盖范围之外表示未观测，不能补成零。已声明范围内的空窗口是确定的零值，不能当成数据缺失。

仿真实际执行区间也与观测声明区间分别保存。若一轮在 75 ms 左右结束而观测声明到 1 s，后面的窗口可为零，但不能据此声称仿真还执行了更多轮次。目标场景的实际区间与使用建议见[CM384/R1 场景](scenarios/cm384_r1.md)。

## 读取与校验

[research_storage.py](../python/moe_ub_cosim/research_storage.py)提供以下常用接口：

| 接口 | 返回或检查的内容 |
|---|---|
| `stored_rows(directory, table)` | 逐条读取实际保存的压缩表；端口表仅返回非零窗口 |
| `logical_windows(observed_directory)` | 按公开配置逐窗恢复完整端口表，包括零窗口和不足周期的尾窗 |
| `logical_queue_windows(observed_directory)` | 对含队列统计的新版观测逐窗恢复等待量；不依赖该窗是否发包 |
| `validate_observed_storage(observed_directory)` | 检查目录白名单、清单、公开字段、非零窗口顺序与边界、端侧记录及文件完整性；只读该 observed 目录 |

计算总字节或非零窗口数时可以只遍历实际保存的行，例如在已安装项目的 Python 环境中：

```python
import json
from pathlib import Path
from moe_ub_cosim.research_storage import stored_rows

observed = Path("runs/demo-10us/observed")
metadata = json.loads((observed / "storage.json").read_text())
nonzero_windows = 0
total_bytes = 0
for row in stored_rows(observed, "port_windows.csv"):
    nonzero_windows += 1
    total_bytes += row["counted_packet_bytes"]
zero_windows = metadata["logical_windows"] - nonzero_windows
print(total_bytes, nonzero_windows, zero_windows)
```

`logical_windows` 适合必须使用完整时间序列的算法。完整网络的逻辑窗口数可能很大，流式接口节省内存，但仍要遍历每一窗；不要先转换为包含全部窗口的列表。非零窗口校验可以直接完成，不需要展开数十亿零行。

读取器检查文件大小、gzip 完整性、记录数、原始字节、CRC32 和单条记录上限。迭代器必须消费到结束才完成末尾核验；读取前几行不等于整份文件已通过。CRC32 用于损坏检测，来源及版本由运行清单中的 SHA-256、输入记录和版本信息另行核对。

`moe-ub validate --observed` 使用上述公开检查；`moe-ub validate --run` 还核对运行清单、文件摘要及已保存的导出校验结果。它不重跑仿真，也不独立证明两个执行完全相同。对照实验的等价性需要比较其实际执行和逻辑观测，已有检查与证据见[首轮验收记录](../fixtures/m10_research_slice/README.md)。

## 后端原始记录

服务输入中的 `record_policy` 指向 `moe-ub-record-storage/1`；底层服务也可通过 `--record-storage POLICY.json` 指定。策略包含以下正整数，未知字段拒绝：

| 字段 | 限制 |
|---|---|
| `max_compressed_bytes` | 原始压缩记录文件的累计内容字节 |
| `max_buffer_bytes` | 共用压缩缓冲的有效载荷上限，最大 1 GiB |
| `max_record_bytes` | 单条原始记录上限，最大 64 MiB；缓冲还必须容纳额外的 64 KiB 压缩块 |

开启后，`execution/result.json` 使用 `moe-ub-service-result/2`，`record_storage.fields` 引用 `execution/records/` 中的 gzip JSONL；没有该策略的旧入口保留 `/1` 数组结果。按图保存的文件在清单中按提交顺序排列，避免多组乱序完成改变原数组的顺序；WQE 启动、完成与目标写入按实际回调顺序记录。

[record_storage.py](../python/moe_ub_cosim/record_storage.py)的 `iter_result(execution, field)` 兼容这两种结果，`iter_port_records(execution, port, name)` 读取端口原始记录并按其清单恢复逻辑空窗口。端口的 `record-storage.json` 使用 `moe-ub-port-records/1` 或增加队列资料的 `/2`，登记表由共享 `registry-records.json` 引用，避免每个端口复制一份操作和传输表。原始逐包事件、FIFO 队列记录、包字节对应和 ASTRA issue/callback 都是内部执行证据，不直接充当算法输入。

`RecordStore` 共用有限缓冲，按块压缩，不为每个端口常驻文件句柄和压缩器；一个文件允许包含多个标准 gzip member。写入前核对压缩预算，失败后不能继续产生成功结果。空记录在封存时创建，之后禁止追加；可通过硬链接复用相同空内容，但各逻辑文件仍分别计入记录策略的压缩字节限额。后端记录缓冲字节只表示有效载荷，不能作为进程 RSS。

## 导出和宿主资源预算

研究导出使用 `moe-ub-research-export-policy/1`，所有字段为正整数。`max_compressed_bytes` 限制 observed、truth、metrics 中压缩记录的合计内容，`max_index_bytes` 限制临时传输索引；`max_record_bytes`、`max_endpoint_records`、`max_operations`、`max_layouts`、`max_id_ranges`、`max_graphs`、`cache_rows` 分别约束单条记录、端侧排序表及各种索引和缓存。单条记录最大 64 MiB。

导出逐条核对计划、交付和业务来源，每个网络传输使用 33 B 定长磁盘索引（其中 1 B 检查原生任务生命周期，旧版为 32 B）。mmap 页面仍计入宿主 RSS；端侧排序和操作、布局表也需要内存，不能把“流式导出”解释为固定且可忽略的内存消耗。通过全部校验后先写 `metrics/index-cleanup.json`，再删除可重建索引；失败保留原因，不写成功的 `validation.json`。

端口采集另有 `moe-ub-observation-resources/1` 数量预算，约束端口数、每端口和总逻辑窗口数、每端口事件字节、包来源条目、登记传输数及导出字节。这些数量与记录策略均不能代替外层宿主预算。普通用户配置中的 `host` 限制覆盖运行阶段实际拥有的子进程与输出根目录：

| 字段 | 计量范围 |
|---|---|
| `rss_bytes` | 周期采样的所属进程树 RSS 合计，包括脱离会话的后代；共享页可能重复计入 |
| `address_bytes` | 每个子进程的地址空间上限，不是整个进程树的总内存上限 |
| `artifact_bytes` | 已登记产物根目录累计占用的上限；另设置单文件大小限制 |
| `wall_seconds` | 当前受监测阶段的现实时间上限，不是仿真时长 |
| `min_free_bytes`、`artifact_roots` | 保留的可用磁盘与一同计量的其他目录 |

外层磁盘监测按设备号和 inode 去重硬链接，跨多个登记根目录也只计一次；不跟随输出符号链接。分别累计唯一文件的内容大小以及文件和目录分配块字节，再取两项总量中的较大值。因此它与“各逻辑记录分别计入压缩内容预算”的内层规则不同，不能互相替代。清单、图、原生诊断日志和目录分配空间也需要外层覆盖。

RSS 和磁盘为周期采样，可能遗漏采样间隔内的短暂峰值；地址空间和文件大小限制为每进程限制。报告中的采样峰值不能宣称为精确物理峰值。运行和导出是分别监测的阶段，均成功才生成完整成功交付；提高一个内层限额不会自动提高其他限额或重试。实现见[host_probe.py](../python/moe_ub_cosim/host_probe.py)。

## 图归档与首轮数据复用

底层完整模型服务的 `--graph-storage POLICY.json` 必须与 `--record-storage` 一同启用。`moe-ub-graph-storage/1` 的 `max_recipe_bytes`、`max_archive_bytes`、`max_active_graph_bytes` 分别限制单图原始构图输入、累计归档和活动图占用。构图在短生命周期 Python 子进程中执行完整生成与校验，服务进程保留编号及路由消费状态，不长期持有已完成大图。

图结束后先确认归档及原文件摘要，写入 `graph-replay/gN/cleanup.json`，再删除已登记且可重建的活动文件。出现额外文件、内容变化或归档损坏时拒绝清理，不能连同未知文件删除。构图前的完整组合校验也在独立进程进行，保留输入和检查摘要。图释放的事件条件由后端参考定义，压缩和磁盘清理不能提前触发 WAVE_DONE。

以下两种复用用途不同，均已保留：

| 入口 | 使用已有资料的方式 |
|---|---|
| `python -m moe_ub_cosim.graph_storage replay` | 从构图输入归档重新生成计划和 Chakra，重新执行校验，并核对源码、固定 proto、计划及图摘要；本身不执行网络 |
| `moe-ub replay` | 从 `slice create` 保存的完整首轮图和初始条件直接运行后端，不重新调用请求 Scheduler、导入原始路由或生成图 |

`moe-ub sample` 则只读取首轮数据包的实际事件，在已覆盖的端口与时间范围内改变采样，不执行上述两种流程。首轮清单绑定输入、来源、图、执行事件、网络与观察范围、随机配置和文件摘要。当前只支持从明确初态执行完整首轮，不恢复任意中途网络状态；改变批次、路由或计算图的实验须重新运行相应前端。

原设计的观测范围与统计要求见 [v0.2 §10](moe_ub_cosim_dev_v0.2.md)，首轮复用边界见 [v0.3 §4.9](archive/moe_ub_cosim_dev_v0.3.md)。存储改造和规模测量的具体结果保留在[资源工程记录](../fixtures/m10_resource_efficiency/README.md)和[首轮验收记录](../fixtures/m10_research_slice/README.md)，不在本格式说明中重复实验过程。
