# 分层通信的逻辑输入与转发门槛

2026-10-27；平台起点`859f0ac`。这是独立的平台增量，不重开已关闭的分层路径研究，也不以平台实现代替两个论文创新的验证。

## 支持与边界

新增公开`scaleout hybrid-deepep-plan`与`hybrid-deepep-replay`。独立格式`moe-hybrid-logical-input/1`声明模型、服务器域、同位置rail、调用中的逐token专家选择及有限资源。域内rank连续，rank=域序号×域内卡数+rail序号；显式rail列表必须与此一致。支持2～4域、每域2～8卡、总rank≤32、每rank≤16 token、1～4调用、BF16/H为256倍数、连续均分专家、fresh布局；不改变任何旧direct/native上限。

plan从输入生成每token每目标域的一份Dispatch数据、域内按GPU去重的转发、rank/expert两个int32计数块（含GIN self-peer）、每源/目标域/通道的周期tail与必需finish。源token按`token % (SM数×scaleout warp数)`分通道，域通道slot是该源通道的有序子序列；它不是最终GPU接收slot。保留Combine的域归约、非expanded按GPU、expanded逐expert三个返回分组，以及原始Top-K位置来源。仅Dispatch/Combine数据记录给出隐藏向量和TokenLayout操作数字节，控制操作单列；均不是线上报文/ACK/重传字节。

replay只检查Dispatch的**数据可见、tail可见和单转发器消费门槛**，动作由调用者声明，不是网络观测。转发器从`channel % 域数`的下一个未结束来源开始轮询，选中后必须等待它；只有等待时刷新各来源tail快照，不能把其他已就绪来源当作旁路。每次最多处理6条，保留每转发器的实际消费顺序，供未来Combine元数据使用。允许回放前缀，未收到零数据finish仍显示等待。最后数据与finish分开，6的整倍数也有独立finish。tail可见须满足其有序数据前缀全部可见；不允许重复/跳过通知或制造不可见数据。

这不是完整Hybrid操作DAG、GPU/NVLink/GIN/provider或时延模型。未执行notify/barrier、TMA与warp资源、最终GPU atomic slot、跨forwarder slot交错、linked-list构建、epilogue、Combine进度/flush/归约数值、源buffer保护与复用、ACK/CNP/恢复。回放结束只表示该Dispatch门槛子模型消费完，不得称collective或网络完成。计数块是接口请求，self-peer不转换为本地零成本。调用之间不自动安排时间，也不允许跨调用推断槽复用。

## 源码依据与验收

固定DeepEP`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`的hybrid_dispatch/hybrid_combine/token/context；先核本地tree、blob与SHA256。保留来源清单与行范围，不下载/执行上游代码。Dispatch重点核336～348、388～460、484～647；Combine重点核360～393、449～576、589～639，排除遗漏Combine尾通知却称全协议的情况。

- 独立逐expert展开核对跨域与域内端点、去重和三种Combine贡献分区；含同GPU多expert、自域数据、不同rail、空输入。
- 0/5/6/7/12/13数据的通知与最后finish；数据先到但无tail、tail缺数据、重复/越序、未知动作、错误rail和预算超限须拒绝。
- 专门检查选中空来源时不能越过它去消费另一来源，以及轮询、缓存刷新、chunk≤6、已结束来源移出轮询。拒绝把消费记录写成真实slot。
- 新cwd公开小流程，保存实际命令/退出状态；重复三产物逐字节一致，复制源码到新路径后公开流程一致；不依赖旧运行、真实数据账户或构建。
- 对旧direct的相关单元及公开流程做受影响回归，固定依赖检查。只验证正确性，不测试研究收益。

## 预算与停止

本轮原件`runs/hybrid_logical_20261027/`，累计300受监控秒/128MiB新增产物，包含复制源码/验收/归档恢复及2MiB收据预留；单命令60秒/RSS1GiB/地址2GiB，磁盘保留128MiB。零新网络、GPU、构建、下载、安装或依赖修改。功能失败保留原件，仅修复有定位依据的平台问题、运行受影响检查，不自动扩场景或限额。规范先本地提交，功能验收后另作本地平台提交，不push；总体研究目标继续`in_progress`。
