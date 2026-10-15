# MoE 推理与 UB 网络联合仿真

`moe-ub-cosim` 用于研究混合专家（MoE）模型推理中的计算、通信与交换机流量之间的关系。它根据推理请求生成计算与通信任务，在模拟网络中执行，再输出端侧操作记录和交换机端口流量统计。

MoE 模型为每个词元（Token）选择部分专家执行计算。当专家分布在不同设备上时，中间数据需要经过网络传送。数据传输的实际完成时间决定后续计算何时开始，也影响下一批请求的处理，因此可以观察网络排队与推理执行之间的相互影响。

## 系统怎样工作

研究者提供模型与部署参数、推理请求、专家选择记录和网络配置。LLMServingSim 负责请求组批，本项目将各轮计算与通信转成执行图，ASTRA-sim 按依赖执行这些任务，ns-3-UB 模拟数据包传输、排队和链路竞争。计算使用配置的耗时模型，不执行模型的数值前向计算。

研究数据包括端侧通信操作的起止时间，以及交换机端口按采样周期统计的包数和字节数。系统另外保存操作与实际网络数据之间的对应关系，供评价关联方法时使用；关联算法只读取明确开放的观测数据。格式和读取方式见[输出数据说明](docs/record_storage.md)。

## 当前能力与验证范围

项目支持多请求连续组批、多层任务以及共享网络的独立专家并行组。首轮或明确初态的有限层段可保存任务图与事件，按已捕获能力重新采样，或重跑后端检查相同业务图在不同网络条件下的执行。

通信可选择既有 WRITE/RTP 或原生 STORE/LOAD，并配置原生逐包喷洒、端侧准备耗时和物理出口服务区间。观测可保存计数、队列等待及窗口聚合，或为指定端口和时间保留细节。[原生首轮示例](examples/native_first_round/README.md)给出不依赖外部数据的入口。

[UB 分层输入基线](examples/scaleout/README.md)提供设备—服务器出口—机架—骨干的拓扑生成、分层带宽、物理放置和路径审计，复用现有 `resolve/run`。它使用 UB 传输抽象，不代表 RoCE 或任意服务器的 NIC 架构。非 UB Scale-Out 的源码比较与接入设计见[ns-3 / Alibaba / ASTRA 研究](docs/reviews/scaleout_ns3_backends_20261015.md)；新在线后端尚未实现。

[Scale-Out 核算命令](examples/scaleout_capacity/README.md)可修改模型通信维度、batch、EP 和两层 Clos 带宽，输出参考通信量及固定交换机数组开销；它不执行网络或验证 GPU 容量。千卡路线、训练/推理区别和热点研究见[规模与热点报告](docs/reviews/scaleout_scaling_hotspots_20261015.md)。

[参数化电交换拓扑](examples/scaleout_topology/README.md)支持两层 leaf-spine、三层 ToR—Agg—Core、multi-rail 和 HPN 参考双平面，分别设置各层数量、上联、端口与带宽。提供四套1024 GPU配置，导出物理图、NIC映射、审计及ASTRA/SimAI文本输入；本命令不执行网络，非UB在线后端仍未接入。

CM384/R1 已有 128 端点完整 61 块首轮的历史参考结果；修正后的 A3 局部资源模板另有有限连续层段验收，不能把两者混成同一模型的全量验证。A3 层段保留完整部署容量和原始层编号，明确声明初态，操作见[A3 层段示例](examples/a3_segment/README.md)。具体参数与范围见[CM384/R1 场景](docs/scenarios/cm384_r1.md)和[模型依据](docs/model_validation.md)。

旧发送槽模板及其[发送资源示例](examples/send_state/README.md)继续保留原用途，不作为新 A3 模型中真实资源数量的硬件结论。

## 运行第一个例子

先按[用户指南](docs/user_guide.md)完成原生 Linux 环境安装与后端构建，然后在仓库根目录运行：

```bash
moe-ub run --config examples/first_round/run.json --output runs/demo-run
```

[随仓库提供的小例](examples/first_round/README.md)使用合成请求和专家选择，无需下载模型或真实路由。运行后 observed 保存研究观测，truth 保存评价用对应关系，其他目录保存输入、核验结果与日志。输出目录须使用新路径。

## 文档导航

| 我想了解 | 阅读入口 |
|---|---|
| 安装、运行、配置与复用 | [用户指南](docs/user_guide.md) |
| 模块协作及实际完成反馈 | [系统架构](docs/architecture.md) |
| 输出字段、采样、时间和字节口径 | [记录格式](docs/record_storage.md) |
| 计算、通信和容量模型的依据 | [模型依据](docs/model_validation.md)、[CM384/R1 场景](docs/scenarios/cm384_r1.md) |
| 代码、依赖、测试与维护 | [开发维护](docs/development.md)、[后端参考](backend/README.md)、[代码与迭代布局](docs/repository_layout.md) |
| 实验目录的关系与历史范围 | [实验总索引](experiments/README.md) |
| Scale-Out 问题、限制和研究步骤 | [千卡与热点研究](docs/reviews/scaleout_scaling_hotspots_20261015.md)、[规模探针](experiments/scaleout_scaling/README.md) |
| 实际进度、预算和交接 | [当前任务](docs/tasks/current.md) |
| 既有研究结果 | [研究结论](experiments/scaleup_issue_screening/conclusions.md)、[历史分区](experiments/scaleup_issue_screening/archive/README.md) |

平台里程碑与旧设计见[里程碑历史](docs/milestone_history.md)和[设计档案](docs/archive/README.md)。新实验方案、目录迁移目标和现有实现分别说明，文档交接不表示新实验已经完成。
