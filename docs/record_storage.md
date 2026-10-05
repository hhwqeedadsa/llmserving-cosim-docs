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

## 公开表与字段

压缩研究格式为 `moe-ub-research-storage/1`。每个输出范围有自己的 `storage.json`，声明逻辑表名、文件名、行数、原始与压缩字节、原始内容 CRC32，以及单条记录上限。实际表放在该目录的 `records/` 下。

| 逻辑表 | 字段 |
|---|---|
| `endpoint_ops.jsonl` | `rank_id`、`local_record_id`、`kind`、`start_local_ns`、`end_local_ns` |
| `port_windows.csv` | `switch_id`、`port_id`、`direction`、`local_window_start_ns`、`local_window_end_ns`、`counted_packets`、`counted_packet_bytes` |

`local_record_id` 只在一个 rank 内编号，不编码全局操作身份。端侧起止时间取实际节点与回调，并按通信模板确定 Dispatch/Combine 的操作范围；不能用首包或末包时刻代替。`direction` 当前为 `egress`，包数与字节数为非负整数，时间单位为纳秒。

`public_config.json` 使用 `moe-ub-observed/1` 或带显式数量限制的 `/2`，声明 rank、观测端口、本地时间范围、周期、时钟分辨率、计数位置和字节口径；不包含时钟偏移与漂移。`observed/` 的压缩格式只允许 `public_config.json`、`storage.json`、`records/` 及上述两张表，校验器不会为了验证它们访问 `truth/`。

**逻辑名以 `.csv` 结尾的压缩表，内部也是 gzip JSONL。** 例如 `port_windows.csv.gz` 解压后每行一个 JSON 对象，应按清单和项目读取器解释，不能根据后缀交给 CSV 解析器。早期未压缩研究输出则直接保存 `endpoint_ops.jsonl` 和真正的 `port_windows.csv`；二者的字段与统计含义相同，文件编码不同。

## 计数位置与字节口径

交换出口成功开始发送时，按当时的 `Packet::GetSize()` 将整包计入窗口。一次包发送全部计入开始时刻所在窗口，不按包在链路上占用的时间拆分到多个窗口。发送尝试、入队和发送结束不是本计数位置；未包含在 ns-3 包中的物理编码或 FEC 字节也不计入。

| 字节类别 | 含义 |
|---|---|
| `business_payload_bytes` | 模板传输的有效激活或专家结果等业务范围；只有这一部分参与操作归属 |
| `application_metadata_bytes` | 量化比例、索引、计数或状态标志等应用数据 |
| `padding_bytes` | 为模板布局或复制对齐而实际发送的填充；内存窗口预留空间不自动计入 |
| `data_protocol_overhead_bytes` | 承载上述数据的包中，剩余协议头等字节 |
| `control_packet_bytes` | TA/TP ACK、信用等协议控制包的整包字节 |
| `unattributed_bytes` | 分类输出保留的未归类计数字段；当前有效执行不以它掩盖缺失的 WRITE 来源 |

应用状态 WRITE 可以只含元数据，仍是应用消息，不能因为没有业务向量就归入协议控制包。TA/TP ACK 即使带任务标签，其载体字节也不获得业务操作归属。旧模板没有应用元数据或填充时相应量为零；`metrics/validation.json` 声明本次是否输出填充字段。

每个包每次经过一个观测出口都计数。跨服务器路径可能经过多个交换出口，因此全网端口字节之和不是唯一交付业务量。唯一交付量从目标有效写入核对；逐端口计数与原生端口累计值核对；每窗分类之和与整包计数核对；来源开启时，该窗各操作归属业务字节之和再与业务分类核对。来源关闭会停用操作归属检查并记录 `attribution_status="disabled"`，不改变原有执行和整包观测。

`truth/attribution_windows.csv` 保存 `operation_id`、端口与方向、本地窗口起止和 `business_payload_bytes`。这里的字节量表示该操作在该端口、该本地窗口中实际经过的业务字节，量大于零才表示二者有真实关联。对应关系从本次包的任务与字节范围产生，不从端侧时间相近或端口流量形状反推。

## 本地时钟、窗口与零值

执行只使用 ns-3 的全局时钟，导出时才转换为设备本地时钟：

```text
local_ns = floor((1000000000 + drift_ppb) * global_ns / 1000000000) + offset_ns
```

`offset_ns` 为有符号纳秒，`drift_ppb` 为十亿分之一的漂移比例，整数计算向下取整。每个 rank 和每个被观测交换机恰有一份时钟，同一交换机的端口共享该时钟；校验正时钟比例和整数范围，保证转换结果单调不递减且不溢出。内部时钟与全局时刻保存在 `truth/clock_models.json` 和对应记录中，不写入公开表，也不改变发包或计算调度。

端口窗口使用本地半开区间 `[start, end)`。落在右边界的发送属于下一窗口；结束位置不足一个周期时仍保留较短的最后窗口。端侧记录保存操作的完整起止时刻，跨越端口观测边界的操作不会被裁剪成另一条操作。

压缩输出只写有包的窗口，`storage.json` 中的 `window_encoding="nonzero_with_declared_zero_windows"` 和 `logical_windows`，结合公开端口范围与周期，声明其余窗口均为零。清单覆盖范围之外表示未观测，不能补成零。已声明范围内的空窗口是确定的零值，不能当成数据缺失。

仿真实际执行区间也与观测声明区间分别保存。若一轮在 75 ms 左右结束而观测声明到 1 s，后面的窗口可为零，但不能据此声称仿真还执行了更多轮次。目标场景的实际区间与使用建议见[CM384/R1 场景](scenarios/cm384_r1.md)。

## 读取与校验

[research_storage.py](../python/moe_ub_cosim/research_storage.py)提供三种常用接口：

| 接口 | 返回或检查的内容 |
|---|---|
| `stored_rows(directory, table)` | 逐条读取实际保存的压缩表；端口表仅返回非零窗口 |
| `logical_windows(observed_directory)` | 按公开配置逐窗恢复完整端口表，包括零窗口和不足周期的尾窗 |
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

[record_storage.py](../python/moe_ub_cosim/record_storage.py)的 `iter_result(execution, field)` 兼容这两种结果，`iter_port_records(execution, port, name)` 读取端口原始记录并按其清单恢复逻辑空窗口。端口的 `record-storage.json` 使用 `moe-ub-port-records/1`，登记表由共享 `registry-records.json` 引用，避免每个端口复制一份操作和传输表。原始逐包事件、FIFO 队列记录、包字节对应和 ASTRA issue/callback 都是内部执行证据，不直接充当算法输入。

`RecordStore` 共用有限缓冲，按块压缩，不为每个端口常驻文件句柄和压缩器；一个文件允许包含多个标准 gzip member。写入前核对压缩预算，失败后不能继续产生成功结果。空记录在封存时创建，之后禁止追加；可通过硬链接复用相同空内容，但各逻辑文件仍分别计入记录策略的压缩字节限额。后端记录缓冲字节只表示有效载荷，不能作为进程 RSS。

## 导出和宿主资源预算

研究导出使用 `moe-ub-research-export-policy/1`，所有字段为正整数。`max_compressed_bytes` 限制 observed、truth、metrics 中压缩记录的合计内容，`max_index_bytes` 限制临时传输索引；`max_record_bytes`、`max_endpoint_records`、`max_operations`、`max_layouts`、`max_id_ranges`、`max_graphs`、`cache_rows` 分别约束单条记录、端侧排序表及各种索引和缓存。单条记录最大 64 MiB。

导出逐条核对计划、交付和业务来源，每个网络传输使用 32 B 定长磁盘索引。mmap 页面仍计入宿主 RSS；端侧排序和操作、布局表也需要内存，不能把“流式导出”解释为固定且可忽略的内存消耗。通过全部校验后先写 `metrics/index-cleanup.json`，再删除可重建索引；失败保留原因，不写成功的 `validation.json`。

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
