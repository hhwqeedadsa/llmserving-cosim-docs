# 有界稀疏注册窗口

接口见[公开使用](../../examples/scaleout_rdma_transactions/README.md#使用大地址范围与稀疏初值)，结果见[独立验收](../../fixtures/sparse_windows/README.md)；执行状态由[当前任务](current.md)维护。

目标：现代direct的workspace分配为16MiB，EP buffer另有对齐分配；现有MRT1窗口只能用至多64KiB的完整hex表示，不能容纳相同地址布局。新增transactions/5显式稀疏零初始化窗口，在保留owner/key/实际地址及访问保护的前提下，只为写过的4KiB页保留字节。零初始化是输入声明，不是从未知张量推导零值；未提供的真实张量仍未知。

输入窗口为owner/key/address/allow_atomic/bytes/initialization/segments，initialization固定zero_fill/1，segments由offset/hex组成且不能重叠。逻辑长度1—64MiB，单窗口至多256页（1MiB）实际页，总16窗口；初始segments至多32段/64KiB。任意写若将超页限，先拒绝整个写，不能部分更改内存、原子结果缓存或重复请求序号。跨页、末端、64位地址溢出和活跃源/输出保护均检查。报告按有序页输出，不展开整段虚拟空间；不改变报文、路径和时钟。

transactions/1—4的输入、默认限制和结果保持；v5支持已有MRT1 put/原子/本地copy及在线会话，不引入self服务、GPU可见性或现代完整执行。功能独立验收包括稠密/稀疏同输入的实际包/完成字段比较、现代布局高偏移实字节转发、只改初值的后续输出、原生绕过CLI拒绝、页上限原子失败、跨页copy/保护及旧回归。完整现代布局是地址依据，不把人工事务命名为真实MoE推理。

预算：原件/dev/shm/moe-sparse-windows-20261023/；每构建/检查60秒、RSS1GiB/地址2GiB、阶段64MiB，仿真5ms、单结果16MiB；全阶段480秒/192MiB，归档成员96MiB/压缩32MiB、tmpfs预留8GiB。仅用已有固定工具链/库隔离构建，不安装下载、不改依赖、不运行GPU或重跑旧失败Prefill/1024。旧入口hash保持。小源码/摘要入Git，大件tmpfs易失且无持久备份；平台/研究分别验收提交，不push。研究主贡献与校准缺项仍未成立，整体目标不结束。
