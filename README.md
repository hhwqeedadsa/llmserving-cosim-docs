# MoE 推理与 UB 网络联合仿真

本项目将推理服务调度、ASTRA 计算/通信依赖和 ns-3-UB 包级网络放在同一仿真时间中执行，用于研究端侧通信操作与交换端口采样之间的关系。通信完成来自实际 UB 事件，研究算法只读取公共观测。

统一用户入口已通过小型真实 UB 流程检查；M10 的完整目标首轮切片与重放验收正在进行。计算与网络参数保留参考模型状态，尚未完成目标硬件校准。当前状态见 [current.md](docs/tasks/current.md)，历史结果见[里程碑记录](docs/milestone_history.md)。

## 安装与构建

需要原生 Linux、Python 3.10+、C++20 编译器、CMake 3.24+、Ninja、Git、protoc 及 protobuf/zlib 开发库。固定维护仓库需要相应 Git 读取权限。小例不需要 GPU、模型权重或真实数据账户。

```bash
git submodule update --init --recursive
python3 -m venv .venv/user
source .venv/user/bin/activate
python -m pip install -e '.[frontend]'
moe-ub doctor
moe-ub build --build-dir build/default --jobs 2
```

安装使用当前源码的 editable 模式。`doctor` 报告缺失项，不自动安装；构建成功后，二进制路径和日志位置保存在 `build/default/backend.json`。

## 运行并复用一个首轮切片

```bash
moe-ub run --config examples/first_round/run.json --output runs/demo-run
moe-ub slice create --from-run runs/demo-run --output data/demo-slice
moe-ub slice inspect --slice data/demo-slice --verify
moe-ub sample --slice data/demo-slice --config examples/first_round/sample.json --output runs/demo-10us
moe-ub replay --slice data/demo-slice --config examples/first_round/replay.json --output runs/demo-replay
moe-ub validate --run runs/demo-replay
moe-ub validate --observed runs/demo-10us/observed
```

采样命令复用原事件，重放命令复用已生成图并重新计算网络行为。输出目录须尚不存在；需要重复实验时使用新名称。每条命令有 `--help`；也可使用 `python -m moe_ub_cosim`。构建在其他位置时，给运行/重放传入 `--backend`。

JSON 内路径相对该配置文件，命令行路径相对当前目录。示例采用合成路由和参考耗时。首版切片从已知空网络、KV 已就绪的时间零开始，只覆盖完整首轮；它不是任意中途状态快照，也不复现后续自适应组批。完整操作、预算、输入字段、输出格式和失败处理见[用户指南](docs/user_guide.md)。

## 输入、结果与代码

| 位置 | 用途 |
|---|---|
| [examples/first_round/](examples/first_round/) | 可复制修改的服务、模型、网络、观测及有限预算 |
| `data/` | 本地来源数据、可移动的研究切片；不提交 Git |
| `runs/<实验名>/` | 生效配置、公共观测、内部真值、核验和资源日志；不提交 Git |
| `build/` | 编译产物与构建日志 |
| [python/moe_ub_cosim/](python/moe_ub_cosim/)、[backend/](backend/) | 可复用 Python 实现与联合后端 |
| [tests/](tests/)、[fixtures/](fixtures/)、[scripts/](scripts/) | 自动检查、小型开发输入、仓库维护与专项检查 |
| [third_party/](third_party/) | 按 gitlink 固定的依赖源码 |

研究输入位于每次结果的 `observed/`；`truth/`、`raw/` 和图文件用于内部生成与验证，不能作为关联算法的输入。压缩观测显式声明零窗口；读取方式见用户指南，不应把稀疏文件缺行当作未观测。

## 进一步阅读

- [用户指南](docs/user_guide.md)：操作集合、命令、配置、目录和重复实验。
- [开发管理](docs/development.md)：任务、Git 和依赖维护；日常使用无需阅读开发任务文件。
- [技术规范 v0.2](docs/moe_ub_cosim_dev_v0.2.md)与[v0.3 增量](docs/moe_ub_cosim_dev_v0.3.md)：执行、观测和真实性边界。
- [后端及旧模块入口](backend/README.md)：保留此前的专项开发操作。
- [环境记录](docs/environment.md)与[模型验证](docs/model_validation.md)：实际命令、证据及未校准范围。
