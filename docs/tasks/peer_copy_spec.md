# 持久事务接入共享机内字节转发

2026-10-27，起点`28637a0`。前轮输入驱动的Hybrid表示和轮询门槛已验收，属于progress；本轮补下一项实质缺口，不重开任何已关闭研究额度，不把普通机内互连集成作为论文创新。

## 问题与明确范围

`Fabric::SendLocal`已有按GPU入/出端口竞争的分块crossbar，但旧`Fabric::Send`拒绝与持久流共存。事务`copy`只在`rank`自己的窗口读写，因此不能让实际收到的跨机字节沿Hybrid域内转发到另一张GPU。不能用重新生成标记或普通跨机RDMA代替实际peer字节。

保留transactions/5，新增显式`peer_copy_model={mode:topology_intra_host_crossbar/1}`。仅启用时，local copy的target允许额外`owner`，必须是同服务器的不同GPU；source仍为本节点rank。只有拓扑/2显式启用的既有crossbar可用，沿其带宽、传播、chunk及共享端口资源，在同一Simulator的实际完成回调写入目标/释放源读保护和目标独占写。该local节点`duration_ns`必须为0，避免固定计算耗时与互连重复计时；source串行lane一直占用到本次转发完成。不同source到同target须共享目的端口，普通本地copy继续固定时长。

peer转发不生成MRT1帧、物理RDMA包、ACK或假目标原子。内部数据来自开始时读取的实际受保护窗口，目标提交前不能读取/覆盖，源lease不提前释放。允许不同GPU使用相同虚拟地址；地址重叠判断及reset冲突按owner区分。不接应用窗口关闭或bounded复制池组合，显式拒绝；NIC注册不授予或撤销本地peer访问权限，声明此模型并非安全隔离/撤权或真实NVLink/TMA校准。

公开操作沿用`scaleout rdma-transaction-probe`和`rdma-transaction-session`，添加独立小例，不添加另一个仿真时钟。Fabric增加只允许同服务器的peer传输入口，复用现有SendLocal资源和统计；持久QP和机外网络继续存在。事务原有1024/256/64/16窗口及session256决策上限均不变。完整Hybrid屏障/slot/Combine/源buffer策略仍需后续输入驱动接入，不能用本功能例宣称已完成。

## 固定验收

人工拓扑2台服务器×4GPU、单rail/plane，机内8Gbps/单跳10ns/chunk64B；远端网络100Gbps/1us，声明参数而非实测硬件。source0/1各把128B不同标记copy到同服务器GPU2的连续两段；两者完成后GPU2从实际256B窗口向另一服务器GPU4发送，验证顺序/来源/源保护。逐chunk独立排程应得peer0/1完成276/340ns、目标端口256B/256ns繁忙及128ns最大排队；peer流不能增加物理RDMA包。所有字节由原始输入和实际MRT1负载/内存记录独立重放。

先做源级/单元和编译检查，再固定最多4次新网络：peer probe、相同工作public dependency session、旧无peer的本地copy/远端probe兼容小例、复制源码/固定新运行时与8库的新cwd peer probe。前两次核对peer与远端因果/真实字节及共享端口；迁移只允许逐hash对应的库根路径/枚举位置差异，不删除真实执行字段。所有条件预先固定，任一网络/语义或预算失败保留原件并关闭剩余新网络，不自动重跑、扩限或选其他输入。至少六类非法peer输入须在创建输出/启动后端前拒绝。

构建只用公开rdma-build的新目录，一次全新配置/编译，固定13项依赖，不安装、下载、升级或更换源码。保留有效-O3编译及加载库身份。源码修正可先用独立syntax/unit定位，不用不完整编译冒充原生执行。网络后续研究另设强基线和收益判据，当前只检验正确性。

## 预算与交付

原件`runs/peer_copy_20261027/`；累计2400受监控秒/2GiB新增产物，含构建、运行时/8库、归档恢复、复制和2MiB/10秒管理预留。构建仅一次，编译900秒/外层1000秒、RSS2GiB/地址4GiB/产物512MiB；其他单命令60秒/RSS1GiB/地址2GiB。单网络1ms/结果16MiB/捕获20000，磁盘保留8GiB。失败保留，不自动提高预算。零GPU、系统安装、下载、依赖改动或push。平台验收、来源/恢复及说明文档完成后仅本地提交；两项创新和总体目标仍进行中。
