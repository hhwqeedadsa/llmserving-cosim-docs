# 现代操作到同一原生网络的功能接入

平台增量，基线`b214516`。上一轮self服务已实际验收并提交，属于进展。整体研究目标仍进行中，本轮不做性能收益研究或宣称真实GIN/GPU时延。

目标是让既有ModernDeepEPWindowRuntime的完整Dispatch—专家—Combine事件，由NativeTransactionSession实际回调驱动：保留全部token、rank/expert计数、四个屏障、self、动态slot/Combine绑定、前缀发布、跨调用缓冲复用及源保护。不是遍历ready并自动制造传输完成。

公开`modern-deepep-native`输入包括既有modern配置、逐rank物理node/设备到NIC/plane映射、明确的功能provider模式、self时长/queue_depth及声明本地准备/专家/归约成本。只支持当前有界2—4 rank、已有最多64连接/1024远端/256本地操作及256次session决策，超限拒绝，不放宽原生期限或额度。预分配连接取实际和所有可能动态slot绑定的有限并集，保持同一communicator/context/source/peer身份跨阶段/调用复用；未使用连接不计作活跃流量或真实GIN QP数。

数据为可追踪的合成标记加显式零填充，按现代布局完整复制/发送，不能称数值BF16专家执行。immutable输入、共享主窗口及功能索引信号域分别注册；信号域是明确的功能地址映射，不声称真实provider地址/rkey。真实token路由可来自已取得的四请求，但不下载新数据。source准备、计数消费清零和本地旁路由实际本地内存copy完成；远端/self计数及signal分别走实际put/fetch-add。self不发交换机包。

显式`target_visibility=native_apply_functional/1`仅把实际目标应用解释为本功能模型的可见事件，绝不证明GPU可见。accepted/visible取实际applied回调，且满足现代前驱；local_complete取实际completed，不能从ready、提交或applied推断。issued取实际native submitted/本地started，源lease与queue保留原规则。控制依赖和slot分配为显式零时长逻辑步骤；本地带成本节点沿现有每GPU串行lane，不伪装CUDA SM并发。保留所有已知区别，输出逐现代事件的原生证据和时刻。

第一次实现先做无网络检查，包括假回调/提前完成/错NIC/slot/成本/容量/未知字段，以及独立核对信号与计数字节、epoch复用及连接身份。然后固定最多4次网络：人工两rank两调用完整健康/同输入一个远端数据包丢失；四原始请求的一个完整调用（只用固定既有选择、H7168/E256/Top8，明确不是完整R1模型）；新Python根/空cwd/包内固定库的独立小输入。实际运行前逐一核算当前输入能否落在原限额内，失败保留，不替换成删控制操作的版本或自动扩预算。任一能力缺口先保存实质原因，再修最小平台问题，平台和研究分开。

独立验收须对照原现代计划和实际native初始/增量提交/回调，解码MRT1负载及物理端点、逐字节重放窗口/source lease/本地copy/reset/原子，核对所有应用完成、信号尾部、源ACK和物理排空；至少拒绝事件假完成、缺self/计数、错源字节/原子值、提前复用、路径/身份篡改。新根离线复算要求摘要逐字节一致，迁移只新增原定第四个小网络，不重跑成功网络。

阶段`/dev/shm/moe-modern-native-20261024/`，累计600受监控秒/384MiB新增存储（原件、固定session运行时/8库、归档、恢复、持久副本及2MiB收据预留），最终管理10秒。每命令60秒、RSS1GiB/地址2GiB、阶段128MiB；网络1ms/result16MiB/capture20000，tmpfs预留8GiB/根盘64MiB。只Python/文档，不重编原生、不改依赖、不安装下载、不执行GPU、不push。初始运行时固定上一轮已验收的session程序与8库，并逐hash核对。本阶段完成只能证明条件功能接入；硬件成本、完整模型、千卡、独立留出及论文机制贡献仍须另外证明。
