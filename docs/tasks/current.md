# 当前任务与交接

状态：`completed`。2026-10-22以更可靠的Scale-Out联合仿真器为主线完成可选超时恢复、系统失败语义及独立MoE验证。只本地修改/提交，未push；依赖与UB入口未修改。

## 目标与交付

- 为固定原生RDMA增加源端真实发送/累计ACK进展计时、Go-Back-N超时重传、指数退避和有限重试；接收重复包通过真实反向ACK重新确认。源端不读取全局接收真值，被动观测不驱动协议。
- 数据就绪、发送完整ACK、QP清理和软件额度释放分别保留；实际服务等待尾部包/ACK及计时器排空。重试耗尽停止整个共享运行、明确失败并保留状态，不伪造成功或额度归还；仿真预算先到另报。
- [整体报告](../reviews/ScaleOut超时恢复与可靠性改进_20261022.md)、[HTML阅读版](../review_artifacts/scaleout_recovery_20261022/architecture.html)、[审阅入口](../review_artifacts/scaleout_recovery_20261022/README.md)：主线、约束、网络与系统修改、机制依据、六个后续改进维度。
- 平台本地提交`0fb863a`：[公开配置和完整流程](../../examples/scaleout_rdma_recovery/README.md)、[功能验收](../../fixtures/scaleout_recovery/README.md)。独立实验及报告提交`210cf69`：[实验设计](../../experiments/scaleout_recovery/README.md)、[结果](../../experiments/scaleout_recovery/results.md)、[数值HTML](../review_artifacts/scaleout_recovery_20261022/report.html)。

## 实际结果

- 恢复19probe/5实际服务门通过，覆盖尾包/最终ACK/NACK/连续丢失、短超时和旧反馈、同刻ACK/超时、双plane、机内crossbar排除、一次回收及尾部排空。原故障11probe/3服务、准入11/9、结构11/3与超额拒绝通过；60项Python、3个纯C++及13依赖检查通过。
- 默认结果与修改前实际二进制相同，无故障恢复除新增恢复报告外相同。详细记录关闭/截断不改变执行；缺失记录不冒充完整历史。公开2层4步服务完成4波、628消息、1512节点，1000615ns，独立rdma-check通过。
- 固定8GPU、两交错EP4、batch/rank4、H1024/E16/K2、1层2步、100G Clos：原生无故障122068ns，开启恢复同值；目标6144B最终ACK丢失后原生5ms未完成，恢复实际重发144B后131105ns完成；持续丢失、最多两次超时重传在297486ns明确失败。共同已提交图逐字节相同、首包路径相同。
- 最终ACK丢失时，接收已齐、源确认6000B、交换机末态队列0，但6144B软件额度仍占用；恢复后69660ns完整确认与释放、消息13/14获准、前端提交下一轮。单消息ACK晚52160ns，整体多9037ns，不能把单通信等待直接算成业务损失。
- 公开流程首次因83388包事件导致结果17933537B超过16MiB读取预算而失败；保留原失败，限制示例包记录1000条后以新目录完成，未扩大预算或隐瞒截断。

## 依据与限制

- 计时原则参考RFC6298第5节；重复确认/有限重试动机参考Linux v6.12 RXE。实现为声明的字节序号Go-Back-N扩展，不是生产RoCE/NIC精确复刻。40μs初始RTO、计算与GPU标签无新硬件校准。
- 不跑千卡、完整Scale-Up或训练；没有自适应RTT/RTO、真实通信库分块、QP复用、故障重路由或单QP失败后剩余业务继续。六项后续维度在报告中明确列为建议；未自动开始下一功能。HTML已检查内容和链接，未做浏览器视觉验收。

## 资源与复核

- 起点`9814c05`；运行根`/dev/shm/moe-recovery-20261022/`，沿用原生构建`/dev/shm/moe-fault-20261021/native/`。主仓`build/scaleout`为UB，不混用。15份构建源码hash与当前一致。
- 构建300秒/并发2/RSS2GiB/地址4GiB；单例60秒/RSS1GiB/地址2GiB/仿真5ms/产物512MiB，研究累计256MiB、运行卷留8GiB。大产物留共享内存，失败不自动扩限。
- [完成核对](../review_artifacts/scaleout_recovery_20261022/completion_audit.md)及[机器摘要](../review_artifacts/scaleout_recovery_20261022/completion_audit.json)：106份已有结果（41份恢复）离线重审通过；HTML重复生成和独立分析逐字节一致，没有额外重跑仿真。
- 约143MiB原始输入、结果、失败、日志、依据、源码与构建证据压缩为11646956B本机备份`runs/scaleout_recovery_20261022/evidence.tar.gz`，2823文件逐项hash核验。归档hash见[archive.json](../review_artifacts/scaleout_recovery_20261022/archive.json)。该压缩包被Git忽略且未上传；共享内存重启会丢失。

## 前阶段入口

保留[结构改进与实践](../reviews/ScaleOut仿真结构改进与实践_20261022.md)、[字节额度与队列研究](../../experiments/scaleout_structure/results.md)、[原创新建议](../reviews/系统视角的仿真器创新方案与修改建议_20261021.md)和[前阶段验收](../review_artifacts/scaleout_structure_20261022/completion_audit.md)。这些是绑定旧提交的历史快照，不改写为新功能验收。
