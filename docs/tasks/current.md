# 当前任务

任务编号 `m1-4-port-provenance`

状态 `in_progress`

范围：v0.2 第 10、13.3 节 M1.4，及 T12/T13 在单任务功能验证中的要求。

执行授权：用户于 2026-09-28 确认“现在继续 M1.4”。M1.3 已在父仓库 cdca5ff、UB 2713a38 保存；开始时工作区干净，13 项依赖检查通过。每项任务通过后自行整理记录并本地提交，不推送；下一项仍由用户确认。当前会话 Full access，不再对已授权的常规本地 Git 操作重复询问。

## 目标与边界

在真实写任务经过的一个交换端口，按成功开始发送时的整包大小累计 egress_packet_start 计数。输出固定本地时间半开窗口及零流量窗口；保存实际 ASTRA SEND/RECV 的开始和完成记录。观测中仅保留 rank 内记录号，内部真值保存操作、传输、UB 任务对应和有效字节范围。

通过源端内部字段和被动通知建立任务字节与包载荷字节的对应，不增加业务头、优先级、定时器或传输。协议控制包计入整包字节，不借用任务标签归入业务有效字节。来源开关不得改变包字节、路径、仿真时序或完成状态；来源不明时明确失败。

本轮只验证已有单份合成 URMA_WRITE/RTP，时钟偏移和漂移为零。只输出验收所需日志，不实现 M2 调度、M5 全量数据导出、丢包实验或硬件校准。

## 允许的动作与文件

允许修改 backend/、fixtures/m1_transfer/、tests/unit/、tests/integration/、README.md、docs/tasks/current.md、docs/environment.md，以及 UB gitlink 和 deps.lock.json 的 checkout_commit/project_patch_commits。

在已有 UB 维护仓库从锁定 2713a38cd42376c655ceb26b80507d65677c0653 创建 task/m1-4-port-provenance 分支。仅在 unified-bus 的 ub-datatype.h、protocol/ub-function.cc、protocol/ub-transport.h/.cc 增加非线载荷的任务偏移和被动包来源通知。复用端口现有 TraComEventNotify 成功发送起点，不改变业务路径。原始 baseline_commit、其他依赖、远端和研究定义不变。不安装、下载或修改全局配置。

新构建目录 build/m1-observation/，一次一个构建、最多 8 路并行。实际命令、目录、输入、退出码和日志保存在 runs/m1-4-port-provenance-20260928T130407Z/。开始时已备份原生启动锁并保存 71 个旧构建关键产物校验值。

## 验收与停止

验证窗口边界、空窗口、整包和业务字节守恒、跨 TA 分段及尾包范围、控制包无业务归属、来源未知拒绝。开关来源记录后比较实际网络跟踪、序列化包、ASTRA 完成和资源状态；另外比较改变统计窗口后的结果。

回归既有六项写用例和非法输入、M1.1 时间/计算、M0 单套 UB 链接，以及原 CSV RTP、phase 依赖、CTP 小例。恢复原生启动锁并检查旧产物不变。保留失败证据，不通过改动预期或技术定义绕过失败。

验证通过后先提交 UB 补丁，再更新父仓库锁文件、gitlink 和记录并本地提交。停止在 M1.4；M2.1 等待用户另行确认。
