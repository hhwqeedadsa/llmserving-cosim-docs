# MoE 推理服务与 UB 网络联合仿真

本项目按 [v0.2 开发指南](docs/moe_ub_cosim_dev_v0.2.md) 逐步实现 LLMServingSim 服务前端、其绑定的 ASTRA-sim/Chakra 执行层与 ns-3-UB 网络后端。目标是生成具有通信操作来源和执行依赖的网络过程，用于端侧通信操作与交换机端口时窗的关联研究。

## 当前状态

M0.1 两项原生起步样例已完成：UB 的四个通信任务在两次运行中均完成，跟踪与统计逐字节一致；LLMServingSim 单实例样例在两次运行中均完成 10 个请求，时钟与逐请求 CSV 均匹配上游参照，执行图的来源路径差异已核验。13 个依赖仍按固定提交检出。M0.2 所选 8 项上游回归的时钟全部匹配，112 个请求内部一致性核验通过；一份同名上游 CSV 属于另一工作负载，差异与原比较失败已记录。M0.3 联合构建尚未执行，联合仿真功能尚未实现，M0 整体未完成。实际命令、环境限制和验证范围见 [环境记录](docs/environment.md)。

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

从主仓库根目录启动 Codex，先读取 [当前任务](docs/tasks/current.md)。M0.2 上游回归任务已完成，当前任务文件保留验收结果和参照限制；下一项 M0.3 联合构建边界确认仍待用户确认。每个任务完成且验证通过后，代码助手整理记录并本地提交，不推送；下一项任务仍由用户确认。

## 目录建立顺序

现在只保存规范、管理文件、只读检查脚本和两个直接子模块。`backend/`、`python/moe_ub_cosim/`、`fixtures/` 和测试目录到对应步骤才建立，不预先填充空类或功能占位实现。

构建产物、运行日志、虚拟环境和外部数据不提交 Git。需要长期保存的实验原始数据另行备份。上游依赖各自保留原许可证；本次不替项目选择发布许可证，也不改变仓库可见性。
