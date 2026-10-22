# 当前任务与交接

状态：`in_progress`。2026-10-22按用户新要求，以更可靠、可验证的仿真器为主线继续实现超时恢复；此前结构改进成果保留如下。只在本地修改、验证和提交，不push。

## 当前恢复目标与验收

- 在锁定原生RDMA上增加显式可选的源端累计ACK进展计时、最早未确认字节Go-Back-N重传、指数退避和有限重试；接收端重复数据通过真实反向ACK响应，不依赖发送端读取接收真值或伪造成功。
- 将协议扩展放在专用模块，经原生NIC协议回调接入；被动观测不驱动协议。默认原生模式不新增行为，不升级或混链依赖。
- 尾包、最终ACK、NACK丢失、连续丢失、迟到反馈、短超时重复包和恢复后的回收一次均须验证；重试耗尽明确失败、保留原始资源与业务状态，不报告SEND或请求成功。
- 同时核验迟到包的网络排空、定时器取消、默认兼容、详细观测不扰动及实际LLMServingSim服务推进；平台与独立恢复对照分别验收和本地提交。
- 平台已完成：19probe/5实际服务恢复门、故障/准入/结构回归、60项Python与3个C++检查通过；公开完整服务示例限制详细包记录后完成，原超额失败保留。证据见[恢复验收](../../fixtures/scaleout_recovery/README.md)。独立MoE恢复对照与报告正在实施，不把平台通过当作研究完成。
- 新产物在`/dev/shm/moe-recovery-20261022/`；沿用下述构建/仿真预算，持久卷仍低于8GiB预留，大产物不落该卷。起点`9814c05`主仓干净，13依赖检查通过，已保存修改前二进制。

## 前阶段交付（9814c05，历史快照）

- [整体报告](../reviews/ScaleOut仿真结构改进与实践_20261022.md)、[HTML阅读版](../review_artifacts/scaleout_structure_20261022/architecture.html)：最终目标、服务对象、限定约束、前端图输入、三项已实施修改、八项后续工作的依据与验收。
- 平台本地提交`7630b98`：每源消息数/完整逻辑字节双约束，严格FIFO及可容纳目的端轮转；常量空间交换机队列积分；源QP确认进度与离线阶段诊断。默认保留旧执行，单条超额拒绝，完整ACK与原生发送映射移除后才归还额度。同机crossbar排除、跨plane共享，不解释成硬件NIC容量。
- [平台功能门](../../fixtures/scaleout_structure/README.md)：新增11probe/3实际服务、原准入11probe/9服务、原故障11probe/3服务通过；55项Python、2个C++检查通过；公开`bytes8192.json`完成4波628消息1512节点、423082ns。默认与修改前实际二进制一致，完整/截断/无明细观测等价。
- [独立研究](../../experiments/scaleout_structure/README.md)、[结果](../../experiments/scaleout_structure/results.md)、[数值HTML](../review_artifacts/scaleout_structure_20261022/report.html)：固定8GPU交错EP4、一层两步Decode的5组条件，另2组独立incast观测校验。研究与平台分别提交，不导入测试驱动。

## 发现与限制

- 8192B字节额度下，轮转113045ns、FIFO122068ns，前者快7.39%，仍较透传54362ns慢107.95%；逻辑字节约束确实生效，实际逐源grant身份顺序发生变化。减少队列积分不能推出降低峰值或改善业务。
- 最终ACK故障：目标6144B已接收、源确认6000B，资源未回收，5ms预算内未完成；交换机末态队列为0。没有伪造恢复。锁定库依赖NACK重传，无通用ACK超时恢复；生产可靠性不能据此推断。
- 独立7→1、每源128KiB压力校验：790次端点CE接收，7流见DCQCN反馈，独立CNP包为0，反馈由ACK/NACK标志承载；PFC暂停通知0。完整/summary执行逐字段一致，结果文件5381140B→41831B，汇总积分一致。这不是MoE真实热点或训练测量。
- 计算仍为A100标签与声明成本，未新测硬件、未跑千卡或完整Scale-Up网络。恢复协议、真实通信分块、QP复用及动态交换机存储仅给出有依据的实施路线，本轮未实现。未做浏览器视觉验收。

## 资源与证据

- 起点`8712976`主仓干净；13锁定依赖检查前后通过，未修改依赖、安装或混链UB。构建复用`/dev/shm/moe-fault-20261021/native`，12份构建源hash与当前源码核对一致；主仓`build/scaleout`为UB，不混用。
- 新运行根`/dev/shm/moe-structure-20261022/`，原始成功/失败结果、命令、资源与源码快照在该目录。构建300秒、并发2/RSS2GiB/地址4GiB；单次运行60秒/RSS1GiB/地址2GiB/仿真5ms/产物512MiB，研究累计256MiB，运行卷预留8GiB，失败不自动扩容。
- 持久卷余量约4.1GiB，所有大产物在共享内存；仅保存约2.83MB压缩证据至`runs/scaleout_structure_20261022/evidence.tar.gz`，1926份文件逐一核验，归档hash见[archive.json](../review_artifacts/scaleout_structure_20261022/archive.json)。它是本机备份；共享内存会随重启丢失。
- [交付核对](../review_artifacts/scaleout_structure_20261022/completion_audit.md)：原报告所有段落/表格内容和本地链接核验通过；HTML重复生成、研究重复分析逐字节一致。
- 完成前按当前源码再次核验：[审计记录](../review_artifacts/scaleout_structure_20261022/goal_audit.json)。12份构建源码和1926份归档文件hash一致；56份运行资源/阶段审计、18份实际服务输出的图/字节/回调/排空重核通过，55项Python与2个C++当前检查通过。没有重新启动仿真或把未实现的后续路线算为本轮成果。

## 前阶段入口

保留[原创新建议报告](../reviews/系统视角的仿真器创新方案与修改建议_20261021.md)、[故障研究](../../experiments/scaleout_faults/results.md)、[消息数准入研究](../../experiments/scaleout_admission/results.md)及[前阶段验收](../review_artifacts/scaleout_admission_20261021/completion_audit.md)。旧报告是交付快照，其阶段断言应按绑定版本复核，不改写成新实施状态。
