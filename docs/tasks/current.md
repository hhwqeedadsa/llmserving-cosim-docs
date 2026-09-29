# 当前任务

任务编号 `publish-maintenance-repos`

状态 `completed`

范围：按用户 2026-09-28 对发布方案的确认，将截至 M3.1 的主仓库和子模块提交保存到 GitHub。M3.1 已在 `4fdc416` 完成本地提交，验收记录保留在 Git 历史和 `docs/environment.md`；本任务不开始 M3.2。

## 目标与边界

在 `baoy21` 账号下使用私有 `astra-sim`、`LLMServingSim` 维护副本，原始来源保留为 `upstream`，可写维护地址作为 `origin`，项目提交保存到 `cosim`。已有 `ns-3-ub` 使用 `cosim`，主仓库使用 `main`。主仓库和 UB 已确认是私有仓库，新维护副本沿用私有可见性，不改变现有仓库可见性。

用户已授权本任务创建必要维护仓库并推送。推送顺序为 ASTRA、UB、LLMServingSim、主仓库；只进行普通推送，不强制覆盖远端。后续开发仍遵守完成验证后自行本地提交、不自动推送、下一任务由用户确认的长期约定。

## 允许的动作与文件

允许维护本机 Git 远端和 `cosim` 分支，创建上述私有维护副本；修改 LLMServingSim 的 `.gitmodules`、主仓库 `.gitmodules`、LLMServingSim gitlink、`deps.lock.json`、`README.md`、`docs/tasks/current.md` 和 `docs/environment.md`。基线 SHA、原上游地址和所有执行源码保持原版本；LLMServingSim 仅新增 ASTRA 维护地址配置提交。

允许从固定父提交递归克隆到独立验证目录，检查所有子模块能否由其记录的远端取回。不使用本机对象库、引用克隆或 `--remote`。不安装软件、不修改全局配置、不下载模型与实验数据。命令和原始输出保存在忽略目录 `runs/publish-maintenance-20260928T163038Z/`，凭证仅通过现有认证机制使用，不保存或输出。

## 验收与停止

先确认本机与远端状态、各目标权限和可见性，再由内向外提交地址和指针变更。检查差异与固定依赖，确认子模块提交已可从维护远端取回后再发布父提交。独立递归克隆需通过 `bash scripts/check_deps.sh`，核对 13 个依赖及四个发布分支的远端 SHA。记录真实命令、退出状态及未通过项，不把源码可获取性等同于新机器构建或仿真通过。

完成后更新发布记录、本地提交并推送本任务记录，保留干净工作区，停止；M3.2 仍等待用户确认。

## 实际结果

2026-09-29 完成发布验收。新建 `baoy21/astra-sim` 和 `baoy21/LLMServingSim` 两个私有维护副本，保留本机原上游为 `upstream`。ASTRA、UB、LLMServingSim 依次推送至 `cosim`，主仓库推送至 `main`；四个仓库均为 private。各远端 SHA 已核对。

ASTRA 保持 `e2a14f5`、UB 保持 `1ba7222`；LLMServingSim 新增地址配置提交 `9aed949`，仅修改 ASTRA URL。主仓库地址、锁文件和 gitlink 提交为 `e3a2f2e`，其后仅整理本任务文档。原始基线、其他依赖提交、执行源码和既有功能验收不变。

独立目录从记录的 HTTPS 远端取回全部 13 个固定依赖，本地与验证目录的 `check_deps.sh` 均返回 0；验证目录及子模块干净，无本机对象库 alternates。初次克隆下载完整历史时被主动中断，其后使用有限历史补齐辅助依赖；用户中断后残留的空工作树及 GitCode 未完成检出已恢复。中断、失败和复验原始输出均保留，详见 `docs/environment.md` 及 `runs/publish-maintenance-20260928T163038Z/`。

本次只检查源码发布与获取，未在新环境重做编译或仿真，不代表模型校准或新功能验收。README 已更新私有仓库访问要求与当前获取结果。最终整理提交并推送本任务文档，停止在本任务；M3.2 尚未开始，仍由用户确认。后续开发继续默认仅自动本地提交，不自动推送。
