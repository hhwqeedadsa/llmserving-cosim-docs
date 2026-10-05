# 用户操作指南

统一入口为 `moe-ub`，等价入口是 `python -m moe_ub_cosim`。本文覆盖已完成小例检查的命令；M10 目标首轮规模验收仍在进行，以 [current.md](tasks/current.md) 为准。

## 环境与安装

使用原生 Linux、Python 3.10 或更新版本、支持 C++20 的编译器、CMake 3.24 或更新版本、Ninja、Git、protoc，以及 protobuf、zlib 的开发库。源码包含固定版本的 ASTRA 和 ns-3-UB；不需要 GPU、模型权重或真实路由账户即可运行下文示例。获取私有维护仓库需要已有 Git 读取权限。

```bash
git submodule update --init --recursive
python3 -m venv .venv/user
source .venv/user/bin/activate
python -m pip install -e '.[frontend]'
moe-ub doctor
moe-ub build --build-dir build/default --jobs 2
```

初始化按 gitlink 获取固定提交，不使用 `--remote`。安装采用仓库内的 editable 模式；当前原生构建和固定依赖仍从该源码检出读取，不能把 Python 包拷到别处就当作完整环境。`frontend` 额外依赖用于构图与调度；仅检查和重新采样已有切片不需要这些 Python 包，也不需要原生后端。

`doctor` 只检查，不安装、不下载。缺失项目显示在输出中；原生头文件和库是否可用最终由 `build` 检查。构建日志在 `build/default/user-build-logs/`，后端位置记入 `build/default/backend.json`。构建会生成固定 Chakra schema 的 protobuf 文件，并保持既有 UB 原生启动配置。`--jobs` 控制编译并发，与仿真的 rank 数无关。

## 最小完整流程

在仓库根目录执行；输出目录应尚不存在：

```bash
moe-ub run --config examples/first_round/run.json --output runs/demo-run
moe-ub slice create --from-run runs/demo-run --output data/demo-slice
moe-ub slice inspect --slice data/demo-slice --verify
moe-ub sample --slice data/demo-slice --config examples/first_round/sample.json --output runs/demo-10us
moe-ub replay --slice data/demo-slice --config examples/first_round/replay.json --output runs/demo-replay
moe-ub validate --run runs/demo-replay
moe-ub validate --observed runs/demo-10us/observed
```

小例为三 rank、一个稠密块和三个 MoE 块，使用合成路由与参考计算耗时。三个请求都在时间零到达，各执行一次解码迭代。模型、放置、请求、通信参数分别在 `service.json` 和 `layers.json` 中；首版统一 `run` 接受 `moe-ub-configured-service/4`，旧入口的其他模式继续通过 [backend 文档](../backend/README.md) 使用。不能将小例延迟当作实际硬件测量。

默认配置引用 `build/default`。使用其他构建目录时，给 `run` 和 `replay` 传入 `--backend /path/to/ns3.44-cosim-service`，或修改配置中的 `backend`。命令可以从其他目录执行：**命令行路径相对当前目录，JSON 内路径相对该 JSON 文件目录**。服务模型和路由在示例中内嵌，不隐式搜索历史 `runs/`。

## 每个操作的合同

| 命令 | 主要输入 | 主要输出/行为 |
|---|---|---|
| `doctor [--backend FILE]` | 当前环境、固定依赖，可选二进制 | 工具/依赖/资源检查 JSON；缺项退出非零 |
| `build [--build-dir DIR] [--jobs N]` | 固定源码、工具链 | 后端、库、构建清单与日志；可增量构建，不修改依赖版本 |
| `run --config FILE --output DIR [--backend FILE]` | 服务、层配置、系统、网络、观测及预算 | 完整服务执行、原始记录和已核验的研究观测 |
| `slice create --from-run DIR --output DIR [--storage copy\|hardlink]` | 已完成的首轮运行 | 可移动的切片目录；默认复制，硬链接要求同一文件系统且来源不可修改 |
| `slice inspect --slice DIR [--verify]` | 切片 | 范围、逻辑字节数、版本与校准状态；`--verify` 读取所有文件核对摘要 |
| `sample --slice DIR --config FILE --output DIR` | 已捕获事件、采样配置、预算 | 新观测和独立核验；不调度、不构图、不运行网络 |
| `replay --slice DIR --config FILE --output DIR [--backend FILE]` | 冻结图、初态、后端、预算 | 本次真实 UB 完成事件驱动的执行，以及研究观测 |
| `validate --run DIR` | 完整实验目录 | 校验封存文件、公共观测及已记录的守恒验收；不重新仿真 |
| `validate --observed DIR` | 仅公共观测目录 | 校验公共字段、记录、窗口及零窗口声明；不读取邻接真值 |

各子命令的 `--help` 是参数入口。输入错误、已有输出目录、数据损坏或运行超限返回非零退出码；失败日志保留，不自动覆盖、续跑或提高预算。重新运行时换一个输出目录。`run` 和 `replay` 分别监测执行、导出两个阶段；只有全部阶段成功才封存成功清单。

## 配置与资源

`run.json` 的 `inputs` 指向服务、层、系统和网络；`observation`、`record_policy`、`export_policy` 指向相应 JSON 文件。`sample` 只需要观测和导出策略。`replay` 默认使用切片的系统、网络和原观测，不能覆盖模型、请求或路由。

`max_simulation_ns` 和观测时刻的单位均为仿真纳秒；`backend_timeout_seconds`、`host.wall_seconds` 是宿主秒。`host` 必须显式给出进程树 RSS、单进程地址空间、累计产物字节、最低空闲磁盘、宿主时限和其他需要合并计费的 `artifact_roots`。目录内硬链接按唯一 inode 计费，磁盘按文件内容大小和分配块中的较大值核算。采样监测可能存在短时峰值遗漏，不能作为操作系统级硬配额。

执行与导出需要不同预算时，`run`/`replay` 可另给 `export_host`，字段与 `host` 相同；省略时两阶段各自沿用 `host`。两阶段串行，不累加模拟时延。示例每阶段上限为 4 GiB RSS、8 GiB 地址空间、300 秒，输出上限 2 GiB，另保留至少 20 GiB 磁盘；这些是上限，不代表实际需要这么多空间。

`record_policy` 限制原始压缩记录和缓冲；`export_policy` 限制压缩输出、传输索引、单条记录及元数据条数。模型 HBM/KV 在服务输入中单独核算，不与这些宿主预算混用。

## 切片与重复实验

当前切片只覆盖**从已知空网络、KV 已就绪初态开始的完整首轮**。存在后续图提交或未来到达的完整服务运行会拒绝冻结；不从多轮日志中任意截一段并假定网络空闲。固定图重放仍有计算依赖和本次接收就绪事件；历史包时间仅用于比较。

将整个切片目录复制到新位置后即可使用。`slice.json` 绑定文件摘要、执行区间、观测范围、输入来源、版本和校准状态；初态与来源字段也应随包保留。摘要用于检测损坏，不是发布者签名。硬链接只是一种本地节省空间的存储方式，传到另一台机器仍需复制实体内容。

已有历史归档可以用 `slice create --import-config FILE` 导入；该配置使用 `moe-ub-slice-import/1`，显式指定旧运行元数据、共享图目录、图归档/清单、执行归档/清单、网络、系统、观测、范围和来源证明。该入口用于有完整文件清单的历史迁入，不会重新下载原始路由、重新构图或猜测缺失字段。普通使用者取得已封存切片后无需运行迁入步骤。

归档按操作需要展开：采样只需原事件，重放只需原图。可在采样/重放配置中指定 `cache_directory` 复用同一份校验后的展开副本；目录自动计入宿主磁盘预算。省略则位于本次输出的 `work/slice/`。缓存绑定切片摘要，损坏、来源不符或上次展开中断会拒绝，保留失败后改用新缓存路径。不把缓存当作另一份原始数据包。

修改采样周期、窗口边界或既有时钟模型，只需修改观测 JSON。支持已有端口的子集，不能请求没有记录的端口或捕获范围外的数据；不能补造丢失事件或打开原先没有记录的来源。端侧操作保留整个首轮的开始/结束，包括跨采样边界的操作。零端口窗口在声明范围内完整存在，与范围外未观测不同。

首版网络扰动仅验收旧 CSV 网络的 `topology.csv` 中逐链路 `delay`（非负整数 `ns`）。可复制网络目录、修改该列，在重放配置中设置 `network`；其他文件、端点、带宽和顺序必须相同。结构化目标网络暂只重放冻结基线。后端参数目录若仅 UB 修订号不同，会保留原切片，记录相同参数表下的版本重新绑定；执行等价性仍需另行比较，不能靠目录相同宣称通过。

## 结果在哪里、怎样读取

```text
runs/<实验名>/
  manifest.json           # 成功/失败、范围、版本、阶段状态和文件摘要
  inputs/                 # 原输入副本及实际生效配置
  raw/                    # run/replay 的内部执行记录
  observed/               # 研究算法唯一允许读取的目录
  truth/                  # 内部来源与时钟，仅供生成和验证
  metrics/                # 字节核验、窗口统计和资源索引清理记录
  logs/                   # 各阶段的命令、stdout/stderr 与资源报告
```

采样输出引用同一个来源切片，不复制原始大图或事件。`observed/` 可以单独复制交给研究算法。其 `public_config.json` 声明字段与窗口，`storage.json` 声明压缩记录；`records/` 中是 gzip 压缩 JSONL，逻辑表名保留 `port_windows.csv`，物理内容不是普通 CSV。零窗口采用明确的稀疏编码；直接读非零文件时不能把缺行当作未观测。

Python 使用者可复用 `moe_ub_cosim.research_storage.stored_rows(directory, table_name)` 读取存储行，使用 `logical_windows(observed_directory)` 迭代完整窗口。目标首轮可能声明数十亿零窗口；研究算法应尽量基于公共配置和非零记录处理，避免不必要的全量展开。该读取器只读取传入的公共目录，不要求真值。

真实目标首轮的来源、参考计算、模型精度差异及硬件未校准状态均保留；取得切片不意味着复现真实硬件的绝对时延。更换模型、批次、路由、专家放置或计算图，需要回到前端重新生成相应实验，不能继续使用旧冻结图。
