# MoE 推理服务与 UB 网络联合仿真

本项目按 [v0.2 开发指南](docs/moe_ub_cosim_dev_v0.2.md) 逐步实现 LLMServingSim 服务前端、其绑定的 ASTRA-sim/Chakra 执行层与 ns-3-UB 网络后端。目标是生成具有通信操作来源和执行依赖的网络过程，用于端侧通信操作与交换机端口时窗的关联研究。

## 当前状态

M0 已完成。M0.1 的 UB 与 LLMServingSim 原生样例可重复；M0.2 所选 8 项上游回归的时钟全部匹配，112 个请求内部一致性核验通过，一份不适用的上游 CSV 已定位来源并保留原比较失败。M0.3 的联合链接探针已构建、运行并通过审计：ASTRA 全部 52 个核心对象进入程序，实际装载的 10 个 ns-3 模块均来自本次 UB 构建。13 个依赖仍按固定提交检出，原生关键产物未变。

M1 已完成：实际 ASTRA SEND/RECV 经 UB 完成一份写事务，接收数据就绪与源事务完成分别计时。在实际交换端口按发送成功开始累计整包字节，保存端侧记录、操作/传输对应和任务至包的有效字节范围。来源开启、关闭和不挂观测的结果一致；写、计算、原 CSV 和链接回归通过。当前仅有合成功能验证所需的观测输出，尚未实现服务调度和 M5 正式研究输出。入口见 [联合程序](backend/README.md)，实际检查与失败原因见 [环境记录](docs/environment.md)。

本项目当前不引入 SimAI、ServeGen、AICB 或 SimCCL，不实现端网关联算法。开发环境为原生 Linux。

## 阅读入口

| 文件 | 用途 |
|---|---|
| [AGENTS.md](AGENTS.md) | 代码助手每次任务遵守的约束 |
| [开发管理说明](docs/development.md) | 目录、Git、子模块和文档的维护方式 |
| [v0.2 开发指南](docs/moe_ub_cosim_dev_v0.2.md) | 技术行为、接口和分阶段验收 |
| [当前任务](docs/tasks/current.md) | 本次允许做什么、怎样检查、何时停止 |
| [环境记录](docs/environment.md) | 本地实际确认的环境和命令，未确认项留空 |
| [仓库准备记录](docs/bootstrap.md) | 本次远端准备的范围、依据和未执行检查 |
| [依赖锁文件](deps.lock.json) | 上游基线、实际采用提交和递归依赖记录 |

## 在 Linux 上开始

使用具有本私有仓库访问权限的 Git 凭证。以下命令用于新克隆；当前开发机已完成后两项初始化与检查，这两项命令本身不执行构建或仿真。

```bash
git clone https://github.com/baoy21/moe-ub-cosim.git
cd moe-ub-cosim
# 先阅读 AGENTS.md、docs/development.md 和 docs/tasks/current.md。
git submodule update --init --recursive
bash scripts/check_deps.sh
```

初始化使用父仓库记录的提交，不使用 `--remote`。ns-3-UB 的 `scratch/ns-3-ub-tools` 来自 GitCode；若拉取失败，保留具体报错，不自行换源或升级版本。原生基线可能保留 ASTRA 自带的其他网络后端源码；后续联合程序仍只能链接 ns-3-UB 一套 ns-3。

`check_deps.sh` 只读取 Git 状态和锁文件，不联网、不切换版本、不编译。返回 0 仅表示它检查的版本和工作区状态一致，不表示依赖可编译或仿真正确。脚本状态及检查范围见 [开发管理说明](docs/development.md)。

从主仓库根目录启动 Codex，先读取 [当前任务](docs/tasks/current.md)。M1.4 的端口计数与来源检查已通过，当前任务文件保留验收结果；下一项 M2.1 将使用固定四 rank 输入生成并执行一层 Dispatch、专家计算和 Combine，仍待用户确认。每个任务完成且验证通过后，代码助手整理记录并本地提交，不推送；下一项任务仍由用户确认。

## 目录建立顺序

现在保存规范、管理文件、依赖检查脚本、M0 联合链接探针、最小计算与写事务后端、合成功能输入、相应测试和两个直接子模块。`python/moe_ub_cosim/` 及其他模块到对应步骤才建立，不预先填充空类或功能占位实现。

构建产物、运行日志、虚拟环境和外部数据不提交 Git。需要长期保存的实验原始数据另行备份。上游依赖各自保留原许可证；本次不替项目选择发布许可证，也不改变仓库可见性。
