# Context身份到实际事务连接

平台增量，基线`32f508d`。上一轮已核486个现代操作，两个关键反馈来源的现代共享身份不同，属于实质进展。整体网络/系统论文目标仍进行中。

目标：交付公开、可复用的连接规划与结果核查入口，把显式communicator/source-rank/context-index/peer身份绑定到已有持久MRT1事务连接。在同一次原生网络中验证相同身份跨操作复用、不同context/communicator隔离、源/目的物理NIC正确。它是现代接入所需的连接层，不声称完成现代DeepEP、GIN provider或硬件时延模型。

输入`moe-rdma-context-program/1`包括communicators、bindings和transactions/5程序（省略connections；remote操作用binding替代connection）。communicator明确requested_contexts、GIN connection数、逐逻辑rank的物理node及本地NIC表。按固定来源index%C、index//C和NIC表映射；分配context按C上取整。binding明确源/peer/context index、plane、两个传输端口及队列深度；不按消息/专家/阶段自动增QP或更换NIC，重复持久身份明确拒绝，不静默合并未知资源。独立communicator可映射相同物理卡但有独立连接身份。MRT1另有反向响应载体，不等同真实GIN/原子ACK。

公开`scaleout rdma-context-plan`在创建输出前完成全部结构、物理可达性、地址、依赖和现有保护检查，输出便携输入、transactions.json及绑定清单。self-peer明确不支持，失败前不启动网络，不能删除现代计划中的必要self操作后称完整模型。暂限现有v5能力（最多32物理GPU/64连接/1024远端操作），不放宽现代性能或千卡验收。QoS、原子协议、可见性仍沿原事务定义。

`rdma-context-check`核原输入/拓扑/协议/程序摘要与执行记录，按实际transport stream检查一次QP创建、物理NIC/端口和全部source TX；完整capture另核源TX及目标RX，不用计划字节代替实际包。验证全程序完成、源ACK排空，输出每binding的实际事务数/发送包/字节和绑定事实；不从ACK推导GPU可见。

验收固定：①手工两NIC、同方向不同context及第二communicator，跨本地阶段再次复用一个binding；put与fetch-add目标实际字节/返回核对。②相同程序首个请求包真实丢失一次，保留源重传/恰好一次目标应用/其他绑定进展。③新源码根、空cwd、包内固定库的独立小型健康操作，不重跑前两个成功网络。共3次网络，不运行旧完整推理/千卡或参数矩阵。新单元覆盖身份/整数/未知字段、独立通信域、同NIC多连接、配额、self、错误NIC/plane、非法程序及拒绝前无输出；现有相关输入检查按影响范围运行。只有新增CLI及Python模块，无C++/依赖变化，默认旧路径源码和二进制身份保持。

新阶段`/dev/shm/moe-context-transactions-20261024/`；累计450受监控秒/256MiB新增存储（源码、结果、包内运行时、恢复、持久副本、1MiB收据预留），最后管理另预留10秒。单命令60秒、RSS1GiB、地址2GiB、阶段产物96MiB；单网络1ms、结果16MiB、capture最多20000事件，tmpfs预留8GiB、根盘64MiB。失败保存，不扩大限额或重跑成功网络；无构建、安装下载、GPU、依赖升级或push，仅本地提交。平台与后续性能研究分开提交；完整现代provider、匹配校准、留出、完整千卡与创新仍须另证。
