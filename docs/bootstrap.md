# 仓库准备记录

日期 2026-09-27。目标仓库为 `baoy21/moe-ub-cosim`。开始时仓库没有分支或文件，默认分支名为 `main`，可见性为 private。此次只准备远端 Git 对象、文档和检查脚本，保持原可见性。

## 本次写入

README 提供当前状态及阅读入口。`AGENTS.md` 要求读取规范和当前任务，限制单步开发及 Git 操作。`docs/development.md` 保存目录、子模块维护、文档分工和检查流程。`docs/environment.md` 与当前任务的实际结果均保留未执行状态。

主仓库记录两个真正的 Git 子模块：`third_party/LLMServingSim` 和 `third_party/ns-3-ub`，使用 gitlink 提交指针，不用普通目录或文本文件代替。ASTRA、Chakra 和其他嵌套依赖保留上游父仓库记录，本次没有向依赖仓库写入修改或建立维护分支。

`deps.lock.json` 记录 v0.2 的核心版本，以及本次从父提交读取的辅助 gitlink。`scripts/check_deps.sh` 和其 Python 标准库实现用于本地只读检查；本次未执行它们。没有创建 CI 工作流，也没有触发编译或仿真。

## 规范文件

`docs/moe_ub_cosim_dev_v0.2.md` 来自用户已确认的 v0.2。按本轮明确要求，仅将第 2.3 节的开发环境句改为原生 Linux，其他技术规则、版本和阶段保持不变。

仓库规范文本的 SHA-256 为：

```text
58d06f24f8ce7d81df902ca516f47e2566ab1bb673d83f184807da6382d99e3c
```

对应 Git blob 为 `3f90d5c8f0754be3f6e755f443c9d0c412b2fa1e`。文档版本仍为 v0.2，开发管理新增内容单独放在 `development.md`，不混入技术修订。

## 已读取的依赖记录

| 父仓库 | 子路径或来源 | 核对内容 |
|---|---|---|
| LLMServingSim `a4053bc` | `.gitmodules` 与 `astra-sim` | ASTRA 指向 `d3469945c50410cac2e727c9ab627670c965db2a` |
| ASTRA `d346994` | `.gitmodules` 与 `extern/graph_frontend/chakra` | Chakra 指向 `30221ab8bfd9212aa9c063cd0b14a7d0a2ea6dd7` |
| ASTRA `d346994` | `extern/helper`、`extern/network_backend`、`extern/memory_backend` | 辅助子模块指针写入锁文件 |
| ns-3-UB `581bf28` | `.gitmodules` 与 `scratch/ns-3-ub-tools` | 配套工具来自 GitCode，指向 `2afc7202f88c132b63c38e8c7dcfc08505b13bb2` |

上述为 GitHub 上固定父提交的元数据检查，不是子模块下载验证。配套工具的 GitCode 提交是否可取回，以及更深层递归依赖是否都可初始化，留待本地任务确认。

## 未执行的工作

没有克隆完整项目到目标 Linux 开发机，没有运行递归初始化或依赖检查脚本，没有安装软件、编译代码、运行上游样例或联合仿真。M0 和 T01 至 T16 均未据此通过。

本次没有为项目选择发布许可证，没有公开私有仓库，没有复制会议原始材料或受限路由数据。技术规范中引用的独立研究定义原件不在本次最小初始化文件内；后续需要时由用户提供，不重建或猜写。

## 本地接续

执行 `docs/tasks/current.md` 的 `repo-local-check`，只确认版本、初始化和环境。通过后停止，由用户指定 M0.1 的第一个原生样例任务。

## 元数据依据

- [LLMServingSim 固定提交的子模块](https://github.com/casys-kaist/LLMServingSim/blob/a4053bc1161872420e1e0607cb3409ef659b828e/.gitmodules)
- [ASTRA 固定提交的子模块](https://github.com/casys-kaist/astra-sim/blob/d3469945c50410cac2e727c9ab627670c965db2a/.gitmodules)
- [ns-3-UB 固定提交的子模块](https://github.com/baoy21/ns-3-ub/blob/581bf28a90740def0e79eafe5423af838c6f687d/.gitmodules)
