# 当前任务

任务编号 `m0-1-llmservingsim-native-baseline`

状态 `in_progress`

范围：M0.1 的一个小型 LLMServingSim 原生样例复现。

执行授权：用户已于 2026-09-27 确认开始下一项 LLMServingSim 原生样例任务。缺失软件的安装仍按根目录约定单独取得授权；本次确认不表示允许升级依赖或修改上游源码。

前置状态：`repo-local-check` 已完成；UB 原生样例的构建、两次运行和后处理验收记录已本地提交为 `88bed688b9e629e18dd95d0a8c32fd4855075d60`，未推送。旧任务说明与结果保留在该提交和环境记录中。

## 目标

使用固定版本的 LLMServingSim、其 ASTRA-sim analytical 后端和 Chakra，运行上游已有的单节点单实例短请求样例，并以相同输入重复一次。保存构建及运行证据，核对 10 个请求完成情况、逐请求输出和上游记录的仿真时钟。

本步骤只验证原生程序的可运行性与可重复性，不执行 GPU profiling、模型推理或目标硬件校准，不实现 UB 联合后端；M0.2 回归和 M0.3 联合构建另行指定。

## 先读哪些文件

依次读取 `docs/development.md`、本文件、v0.2 第 2 章和第 13.2 节，再核对 `docs/environment.md` 的已有事实。检查主仓库与全部相关递归子模块状态。

读取 LLMServingSim 的 `AGENTS.md`、根 README、`serving/README.md`、`scripts/README.md`，审阅 `scripts/compile.sh`、ASTRA analytical 的构建脚本与 CMake、Chakra 打包配置、服务入口、样例配置及原始工作负载。涉及其他路径时先读取适用指令。

## 固定版本与输入

- LLMServingSim：`a4053bc1161872420e1e0607cb3409ef659b828e`。
- ASTRA-sim：`d3469945c50410cac2e727c9ab627670c965db2a`。
- Chakra：`30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7`。

以父仓库 gitlink 和 `deps.lock.json` 再次核对，不更新依赖。采用 `serving/run.sh` 默认启用的样例和 `serving/validate.sh` 的 `single` 场景：

- 集群：`configs/cluster/single_node_single_instance.json`，一个节点、一个实例、一个模拟加速器，TP=1。
- 模型描述：本地 `configs/model/meta-llama/Llama-3.1-8B.json`；性能数据使用已提交的 `profiler/perf/RTXPRO6000/meta-llama/Llama-3.1-8B/bf16/` 与 `profiler/models/llama.yaml`。
- 请求：`workloads/example_trace.jsonl` 的 10 条请求；`--dtype bfloat16 --block-size 16 --num-reqs 10`，保留其他原生默认行为，后端为 analytical。
- 上游时钟参照：`serving/validate-baselines.txt` 中 `single 1663996645`（ns）。此数值是待核对参照，不提前宣称本机通过。

首次执行前保存集群、模型、工作负载、实际使用的性能表及参照文件校验值。输出路径和 run ID 使用本任务独有位置，不覆盖已跟踪的 `outputs/example_single_run.csv`。保存生成的网络/系统/内存配置和 Chakra 图；仅记录性选项可以与示例不同，必须写明。

## 允许的动作与文件

可以检查现有系统与 Python 依赖、运行已审阅的静态依赖检查、配置和构建该样例所需的 analytical 后端、运行服务样例两次并核验结果。只启动一个有界并行构建，最多 8 路；不构建 ASTRA 自带的 ns-3 后端，不运行整个上游回归集合。

缺少系统或 Python 软件时，先保存实际缺项和具体安装方案，取得授权后安装。优先使用项目专用虚拟环境；不直接执行会写入系统 Python 的上游 `pip3 install .`。Chakra 原生构建产生的 protobuf 文件及构建产物只放入既有忽略路径；不得改动 `.proto` 或转换逻辑来适配环境。

允许更新主仓库 `docs/tasks/current.md`、`docs/environment.md` 和 `README.md` 的本任务状态；允许将 `docs/development.md` 开头已过期的进度句改成引用环境记录，不改变开发规则。日志、命令记录、核验脚本与归档写入主仓库忽略的 `runs/`，虚拟环境写入忽略的 `.venv/`。不覆盖或清理未知文件，清理仅限已确认属于本任务的产物。

不修改上游源码、原始输入、基准结果、锁文件、技术规范、`.gitmodules` 或 gitlink，不下载模型权重与大型数据，不创建服务接口或联合程序。若需要源码补丁、版本升级或超出本样例的范围调整，保存证据并交由用户决定。

## 怎样判断完成

构建实际退出 0，Python 前端能够使用固定 Chakra 转换器与本次编译的 ASTRA 后端。两次运行均退出 0、完成 10 个请求，无未解释异常或缺失，不能仅凭退出码标为通过。

逐请求输出应与输入的请求编号、输入/输出 token 数量和到达时刻对应，完成及延迟字段有效。两次关键服务结果和仿真时钟一致，并与 `single` 上游参照核对；有差异必须保留并解释，不改参照值。壁钟耗时和生成路径的差异单独说明，不混入仿真一致性判断。

保留实际命令、工作目录、输入和版本、退出码、日志与输出校验值。结束前核对原生源码和依赖指针未变，更新实际结果。通过后自行整理记录并本地提交、不推送，然后停止，下一任务仍由用户确认。

## 实际结果

2026-09-27，已读取范围相关文档、锁定样例与上游参照，主仓库起点为 `88bed68`，相关依赖工作区干净。任务切换说明单独本地提交；本次用户已明确开始，因此不再重复请求执行确认。

初步依赖检查发现系统缺少 `protoc` 和 Protobuf C++ 开发头文件，Python 缺少 protobuf、Chakra、pyinstrument 及 Chakra 打包依赖。尚未安装软件或执行仿真，安装方案与实际检查证据保存在 `runs/m0-1-llmservingsim-native-baseline-20260927T094128Z/`，后续结果追加于此。
