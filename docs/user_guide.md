# 用户指南

本指南说明怎样安装仿真环境、运行小例，以及复用一次执行结果开展采样和网络重放实验。统一命令为 `moe-ub`，也可用 `python -m moe_ub_cosim` 执行相同操作。

第一次使用时，先完成安装和小例运行。已有首轮数据包的使用者可直接阅读[保存与复用首轮](#保存与复用首轮)。模型与网络的工作方式见[系统架构](architecture.md)。

运行前检查千卡 Scale-Out 的 batch、EP 组数、参考通信量和交换机数组开销，可用 `moe-ub scaleout capacity`，见[独立核算示例](../examples/scaleout_capacity/README.md)。它仅需基础 Python，不执行网络或校验显存；与 `scaleout prepare` 的 UB 输入生成用途不同。

生成两层/三层 Clos、multi-rail 或 HPN 参考双平面的非UB物理拓扑，使用 `moe-ub scaleout topology --config examples/scaleout_topology/1024-hpn-inspired.json --output runs/topology-hpn-new`。各层数量、NIC映射、带宽与端口均在JSON中显式配置；完整参数、四套千卡配置与输出说明见[拓扑示例](../examples/scaleout_topology/README.md)。这一步只生成输入及连通性审计，不能直接交给当前UB的 `run`。

## 安装

需要原生 Linux、Python 3.10 或更新版本、支持 C++20 的编译器、CMake 3.24 或更新版本、Ninja、Git、`protoc`，以及 protobuf 和 zlib 的开发库。小例使用合成输入，无需 GPU、模型权重或路由数据账户。主仓库及其维护依赖需要相应的 Git 读取权限。

```bash
git clone https://github.com/baoy21/moe-ub-cosim.git
cd moe-ub-cosim
git submodule update --init --recursive
python3 -m venv .venv/user
source .venv/user/bin/activate
python -m pip install -e '.[frontend]'
moe-ub doctor
moe-ub build --build-dir build/default --jobs 2
```

子模块初始化取回仓库记录的固定提交，不使用 `--remote`。`pip install -e` 让 Python 包直接使用此处源码，原生构建也依赖这份仓库，因此安装后仍需保留完整源码与依赖。

`doctor` 检查工具、Python 包、固定依赖和资源，不安装或下载软件。`build` 实际检查原生头文件和库并构建后端。构建结果记在 `build/default/backend.json`，日志位于 `build/default/user-build-logs/`。`--jobs` 是编译并发数。

只检查数据包或重新采样时，可在虚拟环境中用 `python -m pip install -e .` 安装基础包，无需 `frontend` 额外依赖或原生后端。`run` 需要完整前端与后端；`replay` 需要已构建的后端，但不需要执行调度和构图。`doctor` 检查的是完整运行环境，因此只准备离线采样环境时可能报告缺项。

## 运行小例

**非 UB Scale-Out** 使用独立的 `scaleout rdma-build`、`rdma-probe`、`rdma-run` 和 `rdma-check`，操作见[RDMA 小例](../examples/scaleout_rdma/README.md)。支持参数化 Clos/Agg、multi-rail 与双 plane 小图，运行实际 LLMServingSim→ASTRA→单一原生 ns-3→下一轮调度。当前为声明计算成本的有限 MoE Decode 片段，不执行完整模型或训练。下面的普通 `run` 命令继续使用 UB 后端。

UB 分层基线可先用 `moe-ub scaleout prepare --config examples/scaleout/prepare.json --output runs/scaleout-prepared` 生成输入，再通过既有 `resolve` 与 `run` 执行。[分层网络小例](../examples/scaleout/README.md)说明设备接入、假设的服务器共享出口、机架出口、rank 放置与路径审计。准备步骤无需原生后端，只生成输入；当前范围为单平面、单骨干 UB 网络。它不能执行 RoCE；非 UB 后端的研究范围见[源码报告](reviews/scaleout_ns3_backends_20261015.md)。

核查原生请求的 worker 额度、共享请求编号池和队首占用者时，可使用[原生小例的请求队列配置](../examples/native_first_round/README.md)。该有界记录只进入评价真值目录，算法输入仍为 `observed/`。

需要观察发送槽是否占满、是否有已就绪任务等待时，使用[端侧发送资源小例](../examples/send_state/README.md)。它通过同一套 `resolve`、`run`、`slice`、`sample`、`replay` 和 `validate` 命令启用并读取记录，无需实验目录或历史产物。

在仓库根目录执行：

```bash
moe-ub run --config examples/first_round/run.json --output runs/demo-run
```

这会模拟三个 rank 的一次完整解码迭代。rank 是分布式任务中的逻辑参与者，其专家放置和网络映射由输入指定。小例使用合成请求与参考计算耗时，网络传输由 ns-3-UB 执行；模型和请求细节见[小例说明](../examples/first_round/README.md)。

命令成功后会输出 `passed: true`、仿真结束时刻和观测目录。默认配置引用 `build/default` 中的后端；使用其他构建目录时，给 `run` 或 `replay` 增加 `--backend /path/to/ns3.44-cosim-service`，也可修改配置中的 `backend`。

**输出目录必须尚不存在。** 示例配置要求运行时至少保留 20 GiB 空闲磁盘，并显式限制内存、运行时间和产物大小；具体字段见[资源预算与失败处理](#资源预算与失败处理)。这些数值是保护运行环境的上限或预留量，不是小例的实际消耗。

命令行路径相对当前目录，JSON 中的路径相对该 JSON 文件所在目录。因此也可从其他目录运行，但需相应调整命令行路径。缺少输入文件或出现未知配置字段时会报错。

原生 STORE/LOAD、逐包喷洒、出口限速及聚合保存的组合用法见[原生首轮小例](../examples/native_first_round/README.md)。其中 `moe-ub resolve --config 场景.yaml --backend 后端路径 --output 新输入目录` 先检查 YAML 中的模型和网络参数，生成可交给 `run` 的服务、层、系统与网络文件；它不执行网络仿真。场景内路径相对 YAML，输出中的 `resolution.json` 保存输入摘要与解析状态。该命令需要 `frontend` 依赖，参数会由指定的后端再次校验。

## 查看结果

一次成功运行会在输出目录中保存以下内容：

| 位置 | 用途 |
|---|---|
| `manifest.json` | 操作类型、成功状态、实际范围、软件版本和文件摘要 |
| `inputs/` | 输入副本和实际生效的配置 |
| `observed/` | 供研究算法读取的端侧操作时间与交换机端口流量统计 |
| `truth/` | 操作与网络数据的实际对应关系、时钟参数，供评价与核验使用 |
| `raw/` | `run` 和 `replay` 的内部执行记录 |
| `metrics/` | 字节核验和观测统计 |
| `logs/` | 各阶段命令、标准输出、错误信息与资源报告 |

研究算法只读取 `observed/`；可以把这个目录单独复制给算法使用。`truth/` 中的对应关系用于评价算法结果，不能作为算法输入。只检查观测数据时执行：

```bash
moe-ub validate --observed runs/demo-run/observed
```

`observed/public_config.json` 描述公开字段和采样范围，`storage.json` 描述文件编码，`records/` 保存压缩记录。端口表的逻辑名称虽然是 `port_windows.csv`，当前物理文件使用 gzip 压缩的 JSONL，即一行一个 JSON 对象；不要直接用 CSV 读取器打开。

没有流量的采样窗口由配置与存储声明共同表示，文件只保存非零窗口。声明范围内缺少存储行表示零流量，范围外则表示未观测。Python 读取器 `stored_rows(directory, table_name)` 读取实际存储行，`logical_windows(observed_directory)` 还原包括零窗口在内的完整序列。大场景可能声明数十亿个窗口，通常应结合公开配置与非零记录计算，避免全部展开。字段、单位及读取示例见[记录格式](record_storage.md)。

## 保存与复用首轮

同一次执行可用于两种后续实验：改变采样方式时，从已捕获的事件生成新观测；改变受支持的网络参数时，用保存的任务重新运行后端。先把完整首轮保存成一个数据包：

```bash
moe-ub slice create --from-run runs/demo-run --output data/demo-slice
moe-ub slice inspect --slice data/demo-slice --verify
```

命令中的 `slice` 指**完整首轮数据包**，包含计算与通信的依赖图、输入配置、初始状态、原始事件及文件摘要。当前只接受从仿真时间零、空网络和已就绪的注意力键值缓存（KV 缓存）开始的完整首轮。存在后续图提交或未来请求到达的服务运行不能直接转成这种数据包；也不能从多轮日志中任意截取一段作为新初态。

`slice inspect` 显示范围、文件大小、版本和校准状态；`--verify` 会读取所有文件并核对摘要。摘要用于发现文件变化和损坏，不是发布者签名。默认复制文件；`--storage hardlink` 可在同一文件系统内节省空间，但源文件和数据包都必须保持不变。

### 改变采样周期

小例原来的端口采样周期为 1 µs。以下命令复用相同事件，生成 10 µs 的观测：

```bash
moe-ub sample --slice data/demo-slice --config examples/first_round/sample.json --output runs/demo-10us
moe-ub validate --observed runs/demo-10us/observed
```

`sample` 不运行调度器、计算图或网络。示例中的 `observation: null` 继承原观测配置，`period_ns: 10000` 覆盖所有选定端口的周期。也可让 `observation` 指向新的观测 JSON；显式 `period_ns` 的优先级更高，生效配置保存在新输出的 `inputs/observation.json`。

新的观测可调整周期、窗口起止时刻、设备时钟的偏移与漂移，并选择已捕获端口的子集。采样时不能增加未记录的端口、请求捕获时间范围之外的数据，或改变原始事件是否包含对应关系记录的设置；原观测的资源声明也保持不变。

上述任意分窗能力要求相应详细事件。使用 `moe-ub-observation-config/3` 时，可通过 `recording.mode="aggregate"` 只保存基础窗口；默认空 `detail_ranges` 不产生逐包资料，也可为所选端口指定一个全局半开详细区间。`mode="detailed"` 保存所有本次观测端口的完整详细范围。完整配置、预算和分类真值示例见[聚合保存小例](../fixtures/m15_aggregate_capture/README.md)，字段见[记录格式](record_storage.md#持续聚合与选择性详细记录)。

先用 `slice inspect` 查看 `capture_capabilities`。只有聚合时，`sample` 允许保持交换机时钟并对齐合并窗口，例如原周期 127 ns 可改为 1270 ns；不能改为 1000 ns、任意移动边界或细分。所选端口若有覆盖整个请求区间的详细发包与队列状态，才可进行相应任意分窗。采样保持原捕获策略与真值类别，程序会在创建输出目录前拒绝能力不足的请求；需要更多范围或不同记录类型时，用显式新观测配置执行 `replay` 或重新 `run`。

需要操作阶段和队列等待统计时，给 `run.json.observation` 指定带显式资源预算的 `moe-ub-observation-config/2`，写入 `statistics: {"schema_version":"moe-ub-observation-statistics/1","queue_waiting_bytes":true,"operation_stages":true}`；完整输入见[统计小例](../fixtures/m14_operation_queue/observation.json)。输出新增 `endpoint_stages.jsonl` 和 `queue_windows.csv` 两张逻辑表，读取方式仍为 `stored_rows`。队列完整序列使用 `logical_queue_windows`，不能把“没有发包”解释成“没有积压”。详细状态已采集时可用同样的 `slice`、`sample`、`replay` 命令；旧的纯发包记录不能补出队列状态。字段含义与采集限制见[记录格式](record_storage.md#v04-操作阶段与等待统计)。

观测使用设备本地时钟，`offset_ns` 的单位是纳秒，`drift_ppb` 是十亿分之一的时钟漂移。它们只改变导出的时间，不改变仿真执行。端口窗口包含起点、不包含终点，即 `[start, end)`；最后一个窗口可以短于设定周期。端侧操作仍保留整个首轮的开始和结束记录，包括跨越所选端口窗口边界的操作。

### 重新执行网络与计算依赖

以下命令读取保存的依赖图，在原生后端中重新执行：

```bash
moe-ub replay --slice data/demo-slice --config examples/first_round/replay.json --output runs/demo-replay
moe-ub validate --run runs/demo-replay
```

`replay` 不重新运行前端调度器或生成计算图。它仍按本次 ns-3-UB 的通信完成事件推进后续任务，因此网络变化可以改变计算开始时刻。保存的历史包时间不会作为本次执行时间直接套用。默认沿用数据包中的系统、网络和原观测配置。

`validate --run` 检查已保存文件的完整性、观测格式以及已有的守恒检查结果；它不重新仿真，也不证明两次运行完全等价。需要验证重放与基线是否相同时，应另做记录比较，维护者入口见[开发说明](development.md)。

## 为自己的实验修改配置

`run.json` 的 `inputs` 指向服务、层、系统和网络输入；`observation`、`record_policy`、`export_policy` 分别指向观测、原始记录和导出预算文件。当前公共 `run` 接口接受 `moe-ub-configured-service/4`，模型和路由需按该格式提供。其他服务模式的开发入口及后端参数见[后端说明](../backend/README.md)。 原生 STORE/LOAD 的小型输入及场景命令见[原生操作示例](../fixtures/m11_native/README.md)；向量操作由模板和后端 profile 共同指定，LOAD 包含实际供数通知。 多平面原生喷洒的配置与路径说明见[喷洒示例](../fixtures/m12_native_routing/README.md)。

| 要改变的因素 | 应使用的操作 |
|---|---|
| 端口采样周期、窗口、设备时钟或已捕获端口的子集 | 修改观测配置后使用 `sample` |
| 旧式 CSV 网络中的链路传播时延 | 修改允许的网络列后使用 `replay` |
| 结构化网络的有向出口速率区间、原生逐包喷洒开关、STORE 请求额度和队列策略 | 修改 `backend.json` 中下述允许字段后使用 `replay` |
| 模型、请求、组批、专家选择、专家放置或计算耗时 | 修改前端输入，重新 `run` 生成任务 |

旧式网络的 `replay` 允许修改 `topology.csv` 的 `delay` 列，值为非负整数加 `ns`，例如 `320ns`。复制原网络目录、只修改该列，再让重放配置的 `network` 指向新目录；文件集合、行顺序、端点、带宽和其他设置必须相同。

包含 `backend.json` 的结构化网络允许覆盖 `port_service`，以及使用原生 STORE/LOAD 时的 `attributes["ns3::UbLdstApi::UsePacketSpray"]`（字符串 `"true"` / `"false"`），以及原生 STORE 的 `attributes["ns3::UbLdstThread::StoreOutstanding"]`（字符串形式的整数 1～65536）。请求额度按 worker 生效；只改变额度不会改变 worker 数、每份任务的字节拆分或端点共享编号池，实际请求顺序、网络竞争及完成时间仍由本次执行决定。新增服务区间时将后端配置格式改为 `moe-ub-backend-config/3`；其余字段、来源声明和目录文件保持原值。软件记录原始文件摘要和前后参数，对副本补充实际覆盖来源并再次执行后端校验。A3 资源图还允许覆盖 `attributes["ns3::UbLdstThread::StoreQueuePolicy"]` 为字符串 `"fifo"` 或 `"producer_round_robin"`，要求一个 worker；没有唯一静态核身份的图拒绝轮转。旧参数目录仅可按明确规则补入 FIFO 默认值，不改原图。核内序列、总额度及请求范围的含义和可复制配置见 [A3 小例](../examples/a3_segment/README.md)。结构化重放尚不接受基础拓扑、链路延迟、其他路由设置或任意属性修改；版本兼容检查见[后端说明](../backend/README.md)。

`port_service` 使用 `moe-ub-port-service/1`，包含正整数 `execution_end_ns` 和最多 4096 个 `intervals`。每条区间写明物理 `node_id`、`port_id`、`start_ns`、`end_ns`、正整数 `rate_bps`。仅指定方向受影响；同一出口按起点排序且不得重叠，区间为 `[start_ns, end_ns)`，到期恢复拓扑中配置的速率。`[0, execution_end_ns)` 可表示整个执行范围的速率覆盖。已在发送的包保持开始时确定的速率。

调整执行预算或重新采集更宽观测时，使用 `moe-ub-replay/2`，显式声明 `max_simulation_ns`，可另给 `observation` 文件；省略观测文件时沿用数据包配置。执行预算必须等于 `port_service.execution_end_ns`，不会自动延长。返回结果和 `manifest.json` 的 `coverage` 分别声明执行完成及选定端口的时间覆盖。窗口不足仍可保存有效部分，但 `validate --run` 默认拒绝把它作为完整观测；`--allow-partial-observation` 仅验证已声明部分。完整运行比较应扩大窗口后重新采集。

端侧额外准备成本在 CANN/原生层输入的 `endpoint_prepare` 中声明，按 rank 的本地 token 数或专家输入数计费，详细字段见[后端说明](../backend/README.md)。已有打包成本已经包含的工作不再重复填写。成本编入计算图，修改后必须重新 `run`。可运行的输入与检查方法见[准备成本和出口服务示例](../fixtures/m13_endpoint_service/README.md)。

CM384 与 DeepSeek-R1 的参数来源、当前可用数据和未校准部分见[参考场景](scenarios/cm384_r1.md)。其中 YAML 是场景设计说明，不能直接传给 `run --config`。正式实验应保留生效的 JSON、软件版本和数据来源，并根据[模型依据与验证范围](model_validation.md)解释结果。

## 资源预算与失败处理

运行、采样和重放配置都必须给出有限的 `host` 预算。它限制运行仿真程序的宿主机器资源，与模型内的设备显存及 KV 容量分别计算。

| 字段 | 单位与含义 |
|---|---|
| `host.rss_bytes` | 字节；监测整个进程树的驻留内存 |
| `host.address_bytes` | 字节；单进程地址空间上限 |
| `host.artifact_bytes` | 字节；累计产物占用上限 |
| `host.min_free_bytes` | 字节；运行中要求保留的空闲磁盘 |
| `host.wall_seconds` | 秒；宿主运行时限 |
| `host.artifact_roots` | 还需计入累计产物预算的目录列表，可为空 |
| `max_simulation_ns` | 仿真纳秒；`run` 的仿真时间上限 |
| `backend_timeout_seconds` | 宿主秒；等待后端结束的时限 |

输出目录和展开缓存自动计入产物预算。资源监测会定期采样，可能漏掉很短的峰值；精确核算规则见[记录格式](record_storage.md)。

`run` 和 `replay` 先执行，再导出观测。可用 `export_host` 为导出阶段另设预算，字段与 `host` 相同；省略时两阶段各自沿用 `host`。两个阶段都成功，整个操作才成功；导出所用的宿主时间不计入仿真时延。示例每阶段上限为 4 GiB 驻留内存、8 GiB 地址空间、300 秒，产物上限 2 GiB，至少保留 20 GiB 空闲磁盘。

`record_policy` 另外限制原始压缩记录和缓冲，`export_policy` 限制研究输出、索引与单条记录等规模，具体含义见[记录格式](record_storage.md)。模型的显存、权重、通信缓冲和 KV 缓存在服务配置中核算。

`run`、`replay` 配置可加入 `"diagnostic_storage": "gzip"`，在后端退出后、导出前逐个无损压缩 `raw/execution/log/` 的文本日志。默认 `plain` 保留原文件。压缩后完整读回核对原始长度与 SHA-256，才释放该文本副本；对应清单写入 `raw/execution/diagnostic-storage.json` 并封入运行清单。可用 `gzip -dc runs/demo-replay/raw/execution/log/log.log.gz` 读取原文。此选项不减少仿真运行时的日志占用，须同时预留当时的文本和一个压缩文件的空间；压缩仍受执行阶段宿主预算约束。

输入错误、已有输出目录、损坏文件或运行超限都会返回非零退出码。运行已开始时，错误与资源报告保存在 `logs/`。先阅读原因，再调整输入或预算，并使用新的输出目录重试；程序不会自动覆盖、续跑或提高预算。各子命令的 `--help` 列出其实际参数。

## 迁移数据包与复用展开缓存

将整个数据包复制到另一位置后，先执行 `slice inspect --verify`，再采样或重放。已经打包的模型、图、输入来源和事件随包保留，后续操作无需重新下载原始路由。重放还需要本机可用的后端二进制。硬链接不是跨机器传输格式，迁移时需要复制文件内容。

包含归档的数据包按操作需要展开：采样展开原始事件，重放展开原图。可在采样或重放配置中设置 `cache_directory`，复用经校验的展开副本；未设置时使用本次输出的 `work/slice/`。缓存属于可再生副本，不能替代原始数据包；其空间仍计入预算。缓存来源不符、数据损坏或上次展开中断时会报错，应查明原因后使用新的缓存路径。

有限层段的展开切片可以用 `moe-ub slice compact --slice 原切片 --output 新切片` 压缩历史执行记录，保留每个原始文件的内容与冻结图。源目录不删除，新切片单独生成清单并绑定原 `slice.json` 摘要。默认复制未压缩的图与输入；同一文件系统内可加 `--storage hardlink` 共享不可变文件，避免压缩时重复占用图空间。使用新切片执行 `replay` 时直接读取原图，不展开历史执行记录；`sample` 则校验并展开所需历史记录，展开空间仍计入预算。当前该命令只接收展开的有限层段 `research-slice/3`，输出 `/4`；不能把完整首轮或任意缺失文件的目录当作输入。压缩需要临时空间，不能以预计压缩率代替运行前的空间预检。

已有历史归档可通过 `slice create --import-config FILE --output DIR` 迁入。导入配置使用 `moe-ub-slice-import/1`，必须显式提供运行元数据、图与执行归档清单、网络、系统、观测、范围及来源证明。此操作适合维护已有完整归档的开发者，不会猜测或补造缺失字段。直接取得完整首轮数据包的使用者无需执行导入。


## 执行有限 MoE 层段

需要反复研究少量连续层时，使用 [A3 层段小例](../examples/a3_segment/README.md)。`segment prepare --config SELECT.json --output INPUT` 从 `moe-ub-run/1` 指向的完整模型输入中选择原始 MoE 层和 Decode 步骤，输出 `INPUT/run.json`；随后使用已有 `run`、`validate`。选择文件格式为 `moe-ub-segment-prepare/1`，字段是 `source_run`、`first_layer`、`layer_count`、`decode_step`，索引从 0 开始。准备阶段沿用来源运行配置的 `host` 上限，日志在 `INPUT/logs/resources/`。

真实路由可使用 `moe-ub-segment-source/1` 来源配置：字段与 `moe-ub-run/1` 相同，但 `inputs.service` 可以指向既有 `moe-ub-evidence-service/1`，其中内层服务须为无请求的完整模型 `service/4`，`compute_profiles` 为空，成本采用完整模型配置。准备程序逐文件核验清单摘要和路由格式，只保留所选层与步骤；不下载数据，不从文件结束推断 EOS（请求自然结束）。清单中的固定来源路径沿用仓库根目录解析规则，其余路径相对配置文件。来源精度、未知项、文件与原始行号在 `preparation.json.route_evidence` 中保留。

准备结果的 `run.json.input_evidence` 指向这份证据；`run` 校验服务、层输入摘要和范围后将它带入 `inputs/preparation.json`，保存包也包含它。图内数据变更需重新准备。有限层段计划默认使用紧凑 JSON，只去掉空白，完整语义及 Chakra 校验仍执行。

选择只改变执行范围。模型权重、专家放置和容量声明完整保留；请求上下文对应所选 Decode 步骤。所有请求须在 0 ns 已就绪并容纳在同一初始批次，首块输入和 KV 明确声明已就绪。不会补造缺失路由，也不将短层段等同于完整推理或稳态。

`facts --run RUN --config FACTS.json --output DETAIL` 离线核对并导出指定 graph/rank/层的内部事实，输入必须是带有封存任务图的原始公共运行。配置、数量及空间上限见示例；超过范围或预算会失败。详细记录在 `DETAIL/truth/`，覆盖清单在 `DETAIL/facts.json`，资源日志在 `DETAIL/logs/resources/`。这些字段仅供核验与评价，不能作为只允许读 `observed/` 的分析输入。

`segment freeze --from-run RUN --output SLICE [--storage copy|hardlink]` 保存完成的层段。`copy` 默认独立复制，`hardlink` 适合同一文件系统中不可修改的共享输入；选择前按产物大小预留磁盘。随后使用已有 `slice inspect`、`sample`、`replay`、`validate`。原 `slice create` 的完整首轮约束保持不变。层段回放只接受初态与范围完整声明的 `research-slice/3`；图内条件变化需要重新运行前端。
