# 当前任务

任务编号 `m1-2-write-transfer`

状态 `completed`

范围：v0.2 第 8.1 至 8.5、10.1、13.3 节 M1.2；只完成源端写事务与接收等待登记。

执行授权：用户于 2026-09-28 回复“确认开始，如果有必要可以使用goal”。M1.1 完成记录已在 `1cc2f0f` 保存；本轮已确认主仓库及 13 个依赖工作区干净，固定提交一致。完成验证后自行本地提交，不推送；M1.3 仍由用户确认。

## 目标与边界

从 UbApp 的完成路径分离 UbTransferExecutor：保存每份在途任务的清理参数与完成接收者，提交原 UB 传输，沿原协议完成事件释放本任务的 Jetty、绑定及执行上下文。CSV 模式继续通知 UbTrafficGen 推进依赖；联合模式通知 UbNetworkAdapter，不加载 traffic.csv、不伪造 CSV phase。

复用 UbUtils 初始化拓扑、路由及传输连接。联合路径只提交一份 URMA_WRITE/RTP；配对 sim_recv 只登记等待。固定 sender_completion=transaction_done；源端实际完成后用当前 ns-3 时间异步调用发送回调，释放一个 ASTRA 发送槽。保存稳定 transfer_id、图/节点身份与 UB taskId 的对应，检查字节数、类型、端点和标签，拒绝无法表示的输入。不保存可能来自栈的 sim_request 指针。

本步使用实际 ASTRA SEND 节点，并从接收适配器登记配对等待；目标端 data_ready 及完整 ASTRA RECV 图完成留到 M1.3。接收等待在源端完成时仍不得回调。本轮不声称整张双端通信图已完成，不用固定延迟模拟通信，不实现 M2 调度、服务接口或正式端口观测。

## 允许的动作与文件

允许修改本步骤所需 backend/、fixtures/m1_transfer/、tests/unit/、tests/integration/、README.md、docs/tasks/current.md、docs/environment.md，以及 UB gitlink 与 deps.lock.json 的 checkout_commit/project_patch_commits。

允许在现有 baoy21/ns-3-ub 维护仓库从锁定 `581bf28a90740def0e79eafe5423af838c6f687d` 创建 `task/m1-2-transfer-executor` 分支，只修改 unified-bus 的 UbApp、传输执行器、必要的事务资源清理接口及 CMake 声明。保留 RTP/CTP、读写与内存原生路径的语义，不修改协议定时、数据包或研究定义。其余依赖源码和指针保持不变；不初始化、升级、下载、安装或改变全局配置。

允许在 build/m1-transfer/ 配置和构建本轮目标、计算回归、M0 探针及原 CSV 入口，每次只运行一个构建、最多 8 路并行。日志写 runs/m1-2-write-transfer-20260928T090505Z/，输入使用小型合成功能样例及原 CSV 样例副本。原生启动锁先备份后按已知路径恢复；保留原生、M0、M1.1 旧构建。

## 验收与停止

记录实际命令、目录、输入、退出码及日志。验证只有一份 UB 写任务、接收等待不产生第二份传输、源端完成及发送回调均一次且时间一致；发送槽、WQE/Jetty 绑定和执行上下文释放，TP 按原配置保留或回收。验证非法输入、重复提交与栈请求生命周期，不以源端完成代替 data_ready。

原 CSV 样例的任务、业务字节与时序同锁定基线对照；重新检查 M1.1 时间/计算及 M0 联合链接边界，联合程序仍只加载 UB 一套 ns-3。失败保留原因，不改预期绕过。

完成后先在 UB 提交补丁，再在父仓库更新锁文件、gitlink 和本轮代码/记录，本地提交并停止在 M1.2。所有样例均为功能验证，模型校准仍未完成。

## 实际结果

2026-09-28 完成 M1.2；任务说明提交为 `47e14e4`。UB 补丁 `2d5509b` 已在 `task/m1-2-transfer-executor` 本地提交，`cosim` 同指该提交；保持原 baseline_commit，父仓库同步 checkout_commit、补丁清单和 gitlink。没有推送。

实际 ASTRA SEND 在 10 ns 发起 409,600 字节的 URMA_WRITE/RTP，WQE 完成、transaction_done、send_done 及源图完成均为 8,622 ns，且各完成通知只有一次。ASTRA 发送槽 0→1→0；执行器上下文、RTP Jetty、双向 TP 绑定和事务元数据全部释放。接收等待没有额外传输，回调为 0，CSV 完成任务数为 0。

两种接口登记顺序结果一致；1 字节例在 188 ns 完成。默认两端各保留 2 个预加载 TP；开启原空闲回收后两端各剩 0 个，包括未使用的空闲通道。接口检查覆盖类型、字节/任务号溢出、描述匹配、重复登记、异步提交及提交前取消；大于 32 位的内部 transfer_id 未被当成 UB taskId。

原 CSV 四任务回归的 39 个跟踪/统计文件与保留原版逐字节一致，业务总量 1,638,400 字节；增加 phase 依赖并显式设置可见延迟 1 ns 后的 39 个文件仍一致。原 CTP 64 字节小例的 18 个文件一致。M1.1 的时间边界和四项计算，以及 M0/计算/写程序的单套 UB 链接检查通过。

首次联合运行因 UB 永久周期死锁检查而在外部 30 秒超时；测试入口现显式运行到 start_ns 后 1 ms，并核对真实完成、资源释放和后续事件，未把该上限作为通信完成时间。单元编译出现回调函数名与 ns3::Callback 冲突，改为明确函数名后通过。TP 回收检查的初版预期误以为只删除在用优先级对应的通道，依据锁定 GetUselessTpns 全量空闲扫描源码纠正了测试；失败脚本、原始输出和基线源码片段均保留。phase 附加回归首次未设置原入口要求的 dependency visibility delay，参照原版即明确拒绝，补齐两侧相同输入后通过。M0 探针首次漏传新图路径被拒绝，完整命令随后通过。详见环境记录，没有修改协议定时或完成语义来规避失败。

已恢复原生启动锁，39 个原生、M0、M1.1 关键产物校验值不变。本机证据位于 `runs/m1-2-write-transfer-20260928T090505Z/`。父仓库整理后本地提交；最终依赖核验另保存在该目录的 `dependency-final.json`。

停止在 M1.2 `completed`。当前只运行了源 ASTRA SEND 图和接收适配器等待；没有目标端 data_ready 或 ASTRA RECV 图完成。未完成 M1 整体、正式观测、服务调度或模型校准，下一项 M1.3 仍由用户确认。
