# EP阶段传输完成的必要依赖

固定来源：DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3` 的 direct Dispatch/Combine结束调用`gpu_barrier(...,true,true,false)`，非NVLink分支flush相关GIN contexts后向各rank发布/等待信号。参考GDAKI实现等待已预留SQ前缀的CQ；异步Python接口保留事件/计算流依赖。来源清单见[调用语义核查](../review_artifacts/invocation_semantics_20261023/sources.json)。这不证明源ACK等于真实CQ、控制信号无成本或当前逐贡献流量等于现代去重后的流量。

服务可显式配置`phase_completion: {"mode":"ep_transport_flush/1"}`，默认缺省保持旧图。每层Dispatch、Combine各增加一个组内完成门限：每rank的零计算GATE节点等待本rank本阶段所有SEND回调、RECV回调和阶段生产计算；组内全部GATE到达后，在同一ns-3时间释放所有等待者。Dispatch门限是专家计算的前驱，Combine门限是归并/下一层的前驱。无本地请求/无消息的rank也明确参加门限，组、层、轮互不串扰。只有本次实际回调能释放，不读取未来时间或历史运行。

这是零协调成本的传输flush必要依赖下界，不是完整DeepEP/CQ/GIN屏障。源确认、目标数据、图退休和网络排空继续分列；门限不要求拥塞状态恢复或CNP排空。GATE仅编码为ASTRA零时长外部事件，不按COMP的固定耗时解释其等待，也不能占用资源后阻止本门限必需的前驱。声明图`moe-scaleout-wave/2`明确包含门限成员；新原生启动参数显式开启模式，旧程序必须在创建网络前拒绝。相同输入缺省不生成GATE、不改变旧消息或图字节。

公开核验必须独立检查：每个门限覆盖完整EP组；同层同阶段SEND/RECV及生产节点为前驱；后续专家/merge依赖该门限；实际门限回调不得早于最后到达，所有回调时间等于最后到达时刻；实际每条消息源确认/接收完成不晚于对应门限释放。数据发送仍仅一次，RECV不重复发起。缺门限、串组、漏前驱、提前回调或新配置配旧图均拒绝。

平台验收先用CPU检查完成顺序、重复/未知成员、独立组与阶段、模型无消息成员、默认兼容和非法字段，再在128卡Clos实际LLMServingSim→ASTRA→原生ns-3验证真实路由的两层两步。全部真实来源仍保持15个独立请求、原始E256/Top8/H7168和声明计算。256/512只离线图核验不算实际网络验收；本轮无故障/无优化收益比较。资源与执行进度只在[当前任务](current.md)维护。
