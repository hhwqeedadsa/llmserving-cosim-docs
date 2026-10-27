# 共享上联负载的宿主成本与加速空间

保持已验收网络不变，仅启用原有旁路宿主计时。CPU是模拟器在宿主机上的工作量，不是被仿真的GPU/网络时间。每输入/执行器只观测一次，不作为稳定速度排名。

**重要限制：现有构建虽标记Release，437个编译单元均以`-O3 ... -O0`结尾，实际为`-O0`。固定原生ns-3的NS3_WARNINGS=ON分支在告警选项中追加-O0，覆盖-O3。本轮没有构建或测量优化版本，不能把当前CPU占比当作优化后基线，也不能用消除该编译设置宣称算法创新。**

| 输入/执行器 | Python CPU s | native CPU s | native CPU占比 | Run exclusive CPU s | 令全部native免费后的CPU工作量倍数 |
|---|---:|---:|---:|---:|---:|
| batch4-modern | 0.739190 | 4.710803 | 86.44% | 0.522468 | 7.373 |
| batch4-generic | 1.148137 | 4.707718 | 80.39% | 0.525963 | 5.100 |
| batch8-modern | 1.722638 | 7.914995 | 82.13% | 0.948149 | 5.595 |
| batch8-generic | 1.330842 | 7.919833 | 85.61% | 0.937273 | 6.951 |

CPU上限的前提是Python工作保持不变、把初始化/报告/会话/网络等全部native CPU都当可消除；比仅优化包处理更加宽松。分母省略外层CLI CPU，不能用作墙钟、千卡或推理加速上限。Run exclusive仍混合网络、传输、内存与事件分派，并非纯包CPU。

| 输入/执行器 | 最后快照已处理ns-3事件 | 快照仿真时钟 ns | 源数据包 | 全部物理发送 | worker/native计时记录 |
|---|---:|---:|---:|---:|---|
| batch4-modern | 11334 | 93925 | 280 | 2528 | 1153/34058 |
| batch4-generic | 11334 | 93925 | 280 | 2528 | 1086/34058 |
| batch8-modern | 17742 | 133377 | 408 | 4000 | 1578/52780 |
| batch8-generic | 17742 | 133377 | 408 | 4000 | 1486/52780 |

最后快照只证明该观测点的事件计数；ns-3事件含非包事件，不等于数据包。完整图是普通冻结图基线，原网络编译成本与真实现代库校准仍未包含。

新增观测网络4次；阶段状态`completed`。错误与未执行条件保留，不增加预算或补跑。

原生CPU的主要exclusive阶段（秒）：

batch4-modern：session_progress=0.982008；session_candidate_validate=0.860804；native_total=0.573373；simulator_run=0.522468；session_drained=0.472623；session_pump=0.373739。
batch4-generic：session_progress=0.980156；session_candidate_validate=0.863952；native_total=0.569132；simulator_run=0.525963；session_drained=0.479466；session_pump=0.372030。
batch8-modern：session_progress=1.649398；session_candidate_validate=1.510537；simulator_run=0.948149；session_drained=0.879024；session_events=0.692702；session_pump=0.667284。
batch8-generic：session_progress=1.658530；session_candidate_validate=1.514521；simulator_run=0.937273；session_drained=0.880490；session_events=0.690741；session_pump=0.667726。

原生占比80%—86%不等于包处理占比。Simulator::Run exclusive仅占已观测总CPU约9%—10%，仍混合传输、内存和事件分派；会话推进、排空检查和候选校验占用较多。原有增量progress已经启用，全量对比仅在未启用的开发宏MOE_VERIFY_PROGRESS_UPDATES内，不能把那段源码误认为运行中的全量扫描。

下一步先独立修复Release构建并建立优化后的强基线，再判断是否投入安全聚合或会话调度优化。原有四次额度已用尽，本轮不编译、不追加网络。是否值得实施包级加速，还需优化后成本与跨多跳/反馈的安全条件；本轮尚未实现新算法或证明论文创新。
