# M10 图与观测资源预算

本入口在完整 Decode 参考图上加入显式宿主资源预算。图生成前计算交付和节点数量；逐包、队列、来源记录在 UB 事件发生时写入文件，导出器逐窗口处理记录。网络仍为同一套 ns-3-UB，发送/接收完成、模型参考成本和原生 Scheduler 反馈语义不变。

本步不是完整 R1/CM384 验收。全部图、传输对象及来源账本仍在内存中，数量由预算约束；计划文件大小不等于对象占用内存，也不限制所有依赖边、运行日志或整个输出目录的体积。

## 图预算

在 configured-service/4 的 `execution.resource_budget` 中放入 [graph-budget.json](graph-budget.json) 的对象。服务将同一预算绑定到所有组和层；只在 layers 中填写预算而不在 execution 声明会被拒绝。独立 CANN layer/3 也可在输入顶层声明该对象，但本步服务累计预算入口仅限完整模型服务。

| 字段 | 含义 |
|---|---|
| max_graph_transfers / max_graph_nodes | 单次完整图的全部本地/跨芯片交付数与 COMP/SEND/RECV 节点数上限。子层也检查。 |
| max_plan_bytes | 每份 plan.json 的 UTF-8 文件字节上限；包含子层重复保留的信息。生成时逐片检查，C++ 解析前检查文件大小。 |
| max_run_graphs / max_run_transfers / max_run_nodes | 整次服务累计上限，包括已经完成的图。Python 构图前和 C++ 激活前独立检查。 |

schema 为 `moe-ub-graph-resources/1`。各限额为正整数，单图上限不能高于运行总量；累计交付数须小于既有 comm_tag 编号空间的 499999900。计划写入 `resource_usage`，目录增加 `graph-resources.json` 并纳入 generation.json 摘要；后端要求旁文件与计划/运行声明一致。前后端结果的 `graph_resources` 提供相同累计账目。

旧输入未声明预算时继续执行原 4096 次交付检查。预算拒绝不会截断任务或改用固定网络时间；已产生的诊断文件保留，超预算图不激活。累计预算不足时，已合法完成的旧图仍作为执行事实保留。

## 观测预算与输出

研究观测配置的 clocks、switches、record_provenance 不变，增加 `resource_budget`，值为 [observation-budget.json](observation-budget.json)。`backend_observation()` 将其转为 `moe-ub-port-observation-set/2` 和各端口 `/2`。使用 cosim-service 与上述完整模型图/运行预算；旧 set/1、port/1 保留原记录方式和限制。

| 字段 | 检查位置与范围 |
|---|---|
| max_ports / max_windows_per_port / max_total_windows | Python 本地时钟配置、公共文件验证和 C++ 原始采样配置分别检查端口及窗口数。 |
| max_event_bytes_per_port | 后端每个端口的 packet-events、egress-queue-audit 和可选 packet_ranges 三类 JSONL 合计字节，逐条写入前检查。 |
| max_ledger_packets / max_registered_transfers | 每个端口来源账本的累计包 UID 与注册传输数；关闭来源输出仍需该账本进行业务/元数据分类。 |
| max_export_bytes | 流式导出器生成的 observed、truth、metrics 文件合计 UTF-8 字节；不包含原始服务、Chakra、后端报告或原生日志。 |

后端只为非零窗口保留计数，导出时仍输出所有零窗口和不足整周期的末窗口。原始每个端口的 resource-audit.json 记录事件字节、逻辑/驻留窗口数、账本大小；三类逐事件数组的驻留条数应为零。账本目前按端口分别保留且不提前回收，端口增多会增加其内存；不能据此声称总内存恒定。

Python 新入口是 `moe_ub_cosim.streaming_export`。旧 `export_records` 拒绝带预算的输入，避免误用旧的全量事件加载路径。新导出器要求前后端图账目一致，仍保留预算内的图/传输注册表，但不加载全部包事件、窗口或归属矩阵；每次处理一个端口的一个窗口。支持与旧接口相同的整数局部时钟、半开窗口、零值及字节分类。若输入丢失、超预算或来源不完整，则报错，不生成通过记录。

公共目录仍只有 endpoint_ops.jsonl、port_windows.csv 和 public_config.json。端侧/端口字段不变；新 public_config 使用 `moe-ub-observed/2`，仅新增静态端口/窗口限额，不暴露全局时间、时钟参数、图身份或逐 Token 路由。仅接受旧 `/1` 的外部消费者需更新 schema 检查；库中 `validate_observed` 同时检查两版，并逐行读 CSV/JSONL。新 metrics/validation.json 为 `/2`，逐窗字节分类改为独立 byte-classification.jsonl。

## 实际验证入口

下列命令已在本机执行，输出目录必须不存在。脚本由已提交的四块小模型派生输入，不下载数据或执行数值内核：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_graph_observation_resources.py \
  --build-dir build/m10-resources --output-dir runs/resource-example
```

脚本包含两轮真实 UB、旧/新记录方式与来源开关、公共文件逐字节对照、预算拒绝及目标状态消息计数。每轮 4716 次交付（4704 次 UB 写、12 次本地交付）、9495 个节点，超过旧交付样例限额。两轮共 36864 B 业务，完成于 1698528 ns；没有用独立流量运行拼接结果。

实际两个端口各声明 6001 个原始窗口，仅 1699 个有流量；公共本地时钟导出共 12002 个窗口，其中 8604 个零窗口。另有 17 端口、85000 个零窗口的文件合同测试，该测试没有实际执行 17 端口网络。

每次服务和新导出子命令在 commands.json / *.time.json 记录墙钟时间和 Linux `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss`。用独立计时进程隔离每条命令，记录的是等待子进程链上的最大单进程 RSS，不是多个进程同时驻留量之和，也不是 NPU/HBM 用量。产物字节在 summary.json，详细结果见 [environment.md](../../docs/environment.md)。

128 rank、每 rank 两专家、双 rank/chip 的目标，仅 Dispatch 状态就有 32512 次交付/层，58 层为 1885696 次；计数检查明确拒绝本目录的小场景预算。未生成该目标完整图，也未将其线性外推为内存或运行时间。后续还需目标完整 fabric/多平面拓扑、足够独立真实请求及组合规模实测。
