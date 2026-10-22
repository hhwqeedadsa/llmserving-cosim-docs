# 独立DCQCN反馈参考模式的功能验收

本增量把上一轮[模型核查](../../docs/review_artifacts/dcqcn_validity_20261022/report.html)发现的反馈差异转为可执行的参考通路，默认模式继续保留。
当前范围是持久原生字节入口、8GPU人工Clos、显式源/反向NIC；没有执行真实Prefill、现代DeepEP/GIN、GPU或生产推理，也没有硬件校准和论文优化收益。

## 实现与来源

`dcqcn_reference.hh`实现NP周期/pending、CNP降速/alpha、时间和实际发包字节两个恢复计数器；`PersistentStreams`将它接到同一个ns-3时钟和实际设备/接收事件。
通知使用原生CnHeader/IPv4/PPP，单独经过反向网络；ACK不携带旧反馈位，CNP不推进确认或释放队列信用。QP保留跨工作状态，关闭前排空pending通知与在途包。

原生`SwitchNode::GetOutDev`对协议255没有初始化哈希第三字。`CnpSwitch`只将局部选路元数据投影到已有ACK分支，明确第三字由fid重复拼成，真实报文仍为255、高优先级服务仍为队列0。没有修改固定依赖，也没有把不确定的原生CNP路径当作对照。
按实际CNP UID检查了下表全部398跳，独立重算反向最短候选与Murmur哈希；数据/ACK路径保持原生规则。

参考DCQCN 2015图6、图7和式1—4；实现按图7先递增触发方计数再判阶段，超增步长采用显式固定值。NP周期从生成/入队开始，实际TX可以更晚；RP同纳秒回调遵循ns-3 FIFO。
原文部署参数、原生帧格式及本地取整规则分别声明，不能称逐位复刻生产网卡。配置、来源与约束见[用户说明](../../examples/scaleout_rdma_streams/README.md#可选的独立拥塞反馈参考模式)。

## 实际验收结果

| 人工条件 | 实际源数据包 | 目的端CE | 独立CNP | CNP路径跳数 | 最后工作ACK / 物理排空，ns |
|---|---:|---:|---:|---:|---:|
| 短周期/4KiB恢复阈值 | 411 | 370 | 22 | 118 | 58,414 / 58,414 |
| 文献周期/字节示例＋声明成本 | 411 | 370 | 6 | 32 | 56,035 / 62,929 |
| 增加64KiB反向背景 | 477 | 370 | 23 | 124 | 58,551 / 58,551 |
| 1MiB反向背景持续到反馈阶段 | 1,460 | 370 | 23 | 124 | 101,208 / 101,208 |

这四行不是优化对比：恢复参数或背景工作量不同。第二行说明最终应用ACK与网络反馈排空的时间不可混用；后两行不是训练/生产流量证据。

第一行在真实执行中记录33次timer恢复、6次alpha更新及2个发生字节恢复的源包；最后一行分别73、25、3。每个源包的模型帧长、实际TX时刻和CNP到达均由设备记录核对，独立Python数值重放检查当前/目标速率、alpha、两种计数器、剩余字节及截止时间；额外把发包时QP速率和`next_avail`与重放状态核对。
四条件共4,657条协议状态记录通过，NP通知时间从实际目的端CE序列独立计算。重复和开关被动速率采样保持网络结果完全一致。

原64KiB背景在反馈前已排空，CNP没有交换机等待，不能用它证明忙反向路径已覆盖。追加的1MiB人工用例仍在原1MiB/工作及5ms限制内，实际CNP源队列最大等待78ns，15处交换机等待非零、最大79ns，证明反馈通过实际竞争路径后才到达RP；不是人为添加反馈延迟。

核心手工状态oracle共326项断言，含首次/多次/边界/空闲通知、两种同纳秒NP插入顺序、降速/alpha/两种恢复阶段/重置/取整/下限和独立累计字节关系。严格C++17编译通过；9项Python输入单元检查通过。

默认模式的新旧完整网络对象一致；共享库路径集合相同，仅动态加载枚举顺序不同。最初把该顺序也强制相等导致检查器失败，旧脚本/输出保留；只修比较方式并按输入/二进制hash复用成功条件，没有重跑旧条件或修改网络迁就断言。
最终检查又按当前oracle只读复核全部已成功条件，并执行一次有意的事件容量溢出：明确失败，无成功result、不截断或扩容。CLI/native均拒绝布尔gain。

旧速率观测12条公开命令、持久流程7条、远端事务8条回归通过，分别保留预期拒绝。四个入口隔离构建成功，旧runtime逐hash未变。没有改子模块、安装或下载。

## 独立复核与复现

已有固定依赖和构建工具时，按[用户说明](../../examples/scaleout_rdma_streams/README.md)构建；接口没有新增依赖。保存新旧二进制于不同目录，不覆盖旧文件。纯状态检查：

```bash
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ibackend/rdma \
  tests/unit/dcqcn_reference_test.cc -o /dev/shm/my-dcqcn-state-test
/dev/shm/my-dcqcn-state-test
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p scaleout_streams_test.py
```

公开流程检查从一个新的输出目录生成输入和网络，子命令从该新cwd执行，仿真5ms/宿主60秒/RSS1GiB/地址2GiB/产物64MiB：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_dcqcn_reference_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-stream-probe \
  --reference-backend /absolute/old/ns3.42-moe-rdma-stream-probe \
  --output /dev/shm/my-reference-check
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_dcqcn_reference_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-stream-probe \
  --reverse-busy-from /dev/shm/my-reference-check --output /dev/shm/my-reverse-busy
```

外层应按已有`moe_ub_cosim.host_probe`设置上述限制和8GiB空闲预留。普通流程8个网络条件中7个成功、1个是有意容量拒绝；随后追加一个忙反向条件。
`--reuse-completed 已有检查目录`可重复传入多个根，只复用实际结束、输入与二进制hash一致的成功条件，失败网络不能复用或自动重试。
`--audit-results 已有检查目录 --output 新目录`只读重分析原三参考条件，不创建新网络进程。原始输入和输出都在检查点内，复核不需要真实路由数据或账户。

[最终功能核验](verification.json)、[忙反向核验](reverse-busy.json)、[构建](build.json)、[命令与资源](maintenance.json)、[归档](archive.json)和[HTML](report.html)分别保存证据。
每阶段60秒/1GiB/2GiB，任务总192MiB/480秒，内存盘预留8GiB；失败均保留，没有抬限。根卷低于预留，原始检查点仅在`/dev/shm`，易失且尚无持久备份；Git保留源码与小型证据。旧ELF保留原RPATH，不把归档称为可迁移运行时镜像。

## 研究去留门槛

本次交付的是功能参考模式。没有检验旧真实Prefill长尾在新模式下是否保留，没有生产参数、独立请求、现代在线库或新算法收益。
后续必须先独立预注册机制对照：模式切换同时改变通知生成、反向服务量、RP规则和参数，不能把整模式差异归给一个timer或NP周期。参考模式和旧模式都不是硬件真值。
若可信对照消除候选现象，应收缩研究；若保留，仍须与出口服务量、已有网络感知放置和拥塞控制强基线比较。论文目标继续进行中。
