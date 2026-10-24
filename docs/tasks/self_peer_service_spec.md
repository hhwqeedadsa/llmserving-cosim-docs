# 显式self-peer服务与共享目标内存

平台增量，基线`7549219`。上一目标轮完成连接身份接入、实际包/内存/新根验收及本地提交，属于实质进展。整体网络/系统论文目标仍进行中。

固定DeepEP的GIN屏障包含self信号，专家计数块也进入provider；显式本地token/rank-count旁路与这些操作不同。现有事务禁止self，本增量增加显式、可证伪的功能服务，不能以删self、伪造交换机路径或ACK替代。

仅transactions/5可选`self_peer_model={mode:declared_service_fifo/1}`。self连接仍用id/src/dst/plane/source_nic/destination_nic/queue_depth，省略请求/回复wire端口，增加`self_service={apply_ns,complete_ns}`。src==dst、两端NIC相同且真实存在，0<=apply_ns<=complete_ns<=网络期限。每个self连接独立FIFO，不占GPU本地计算槽，不声称真实NIC竞争、PCIe/loopback路径或成本；其queue是功能应用额度，不称硬件QP。至少保留一个正常远端连接，沿用现有网络校验边界，不为本阶段放宽规模。

put/source-put/fetch-add在原生同一Simulator时钟执行。按已有发布时刻捕获源字节/持有lease，真实服务apply回调对同一RemoteMemory按NIC权限执行，complete回调才回收槽/源lease并放行后继。FIFO按照实际发布，跨连接不添加顺序。原子返回来自实际内存旧值。self结果显式标识服务开始/完成及无包路径，request_acked_ns/response_ns为−1，不生成网络ACK或响应帧；依赖self的request_acked在分配网络前拒绝。applied与completed分别可用，后者不代表GPU可见或真实GIN CQ。

正常连接仍走原MRT1与真实包，默认输入行为保持。公开`rdma-transaction-probe/session`支持上述输入；context planner继续拒绝self，待现代映射阶段再接，不声称本轮运行完整现代DeepEP。与现有显式前缀发布、地址/源保护、静态NIC授权和本地等待兼容；不支持bounded_buffers。在线整批失败须在注册/服务事件前拒绝，保留既有事务保护。

验收4次网络：①新程序默认路径与旧已保存健康结果核对（仅规范化同hash库路径）；②混合远端、自服务、独立本地计算，实际接收后触发self原子并用本地窗口条件驱动后继包，另有同队列两个原子检验FIFO及两个连接不共用GPU槽；③相同程序一个远端包真实丢失，self不重做、目标内存恰好一次；④新Python根/空cwd/包内库的独立小输入。不得重跑成功网络。独立解码远端MRT1/端点/ACK，按真实self回调和字节核对原子/完整内存及所有后继；保存非法self/NIC/时间/端口/ACK依赖的Python与native分配前拒绝和篡改负例。

预算：`/dev/shm/moe-self-peer-service-20261024/`，累计600受监控秒/384MiB新存储（对象、程序、同hash8库、结果、归档/恢复/持久副本和2MiB收据预留），最终管理10秒。每命令60秒/RSS1GiB/地址2GiB/阶段128MiB，最多4次网络、每次1ms/result16MiB/capture20000。tmpfs预留8GiB、根盘64MiB。仅增量编译RemoteTransactions与两事务入口消费者并链接两程序；不重编依赖/旧二进制，不安装下载/GPU/push。失败保留，不提高限额或重复已成功执行；平台与后续研究分开提交。
