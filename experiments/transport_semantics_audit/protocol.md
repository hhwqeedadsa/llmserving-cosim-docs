# 事务功能载体与网络评价边界：离线核查

2026-10-27，平台起点`120a07c`。上一轮CLEM源码核查是progress：它改变了真实CCL、内存复用和持久QP的强基线。本轮只判断现有MRT1网络评价还缺哪条可信参考，不实现新平台功能，也不将差异直接称作创新。

## 问题及竞争解释

1. 多WQE是否被流载体合包：已读`PersistentStreams::Next`发现其按work边界截断，预期实际源包不会跨相邻MRT1帧。若原件支持，应明确排除此解释，不能继续将它列为缺陷。
2. 32B功能帧头是否足以解释明显误差：按每帧操作数字节计算包数与串行字节变化，保持每work边界，仅作删去功能头的计数参考。它不是RoCE报文，也不用于重新推算时延；若当前MTU下包数不变，不新增去头平台功能或声称可提速。
3. 原子回复的网络形态是否与目标RC机制不同：本项目远端fetch-add产生请求字节流、请求ACK、反向Reply字节流及Reply的ACK；缓存Linux v6.12 RXE的成功分支用Atomic ACK携带旧值。核查实际每类传输、回复ACK与应用完成/排空的关系。RXE只是具体软件RC参考，不替代NVIDIA NIC、GIN CQ或故障路径；不能由协议差异直接减去时延、声称收益或判定漏洞。

## 固定输入与范围

只读已关闭的`runs/ecmp_network_choice_20261027/stage/`中batch4的single与quad现代执行，不重开这两个网络。由原归档SHA256 `25ce43a9dc4d72c402df935529d1083dd2801d236b85131ad5646762fc758e40`及成员manifest核对后复制：共同`observed/batch4/{input,plan}.json`；每条件的`result.json`、`initial.json`、`modern-execution.json`、`protocol.json`、`topology.json`。完整网络行为留在truth，输入计划与源码放observed；分析不是在线优化算法，不使用truth提出事前预测。

固定源码：本仓库`remote_memory.hh`、`remote_transactions.cc`、`persistent_streams.cc`及锁定原生`rdma-hw.cc`；本地`data/research/rc_atomic_20261022/`三份原文及sources.json。前者核Git blob/当前HEAD，后者核已保存SHA256，Linux只已记录tag v6.12，未取得固定Git tree，不能升级证据等级。只沿RXE实际atomic执行/ack成功分支与header字段核对，不下载、不补其他模块来声称全协议验证。

不改变输入batch、模型、路由、时刻、源绑定、包序和既有判据；保留原四核心严格比较的回调排列失败，不重新比较或改变其状态。输出包括逐帧/源包边界与字节分解、实际请求/回复/ACK形态、应用完成之后仍在途的回复确认，以及对两个创新候选的下一行动。所有“删头”量只是假设计数，不是生产开销或可消除时延。

## 验收与预算

运行根`runs/transport_semantics_audit_20261027/`；累计360受监控秒/128MiB新产物（含复制源码/归档恢复），单命令60秒/RSS1GiB/地址2GiB，保留8GiB空闲及2MiB/10秒管理预留。零新网络/构建/GPU/下载/安装/依赖修改/push。失败保存，不重跑原网络或扩大条件/预算。

准备先验证旧归档及成员hash；分析不导入平台内部、tests或fixture执行器。独立检查以另一种逐包区间/逐字节方式重建帧、核原子旧值与实际反馈方向，拒绝边界、body、stream及时间篡改。复制研究代码到新路径、空cwd仅离线重算，结果逐字节一致。摘要/中文网页入Git；源码原件、详细记录和封存留运行目录。平台修复须另立规范、验收和提交。本阶段不以强基线之外的收益为成功条件，总目标继续进行中。
