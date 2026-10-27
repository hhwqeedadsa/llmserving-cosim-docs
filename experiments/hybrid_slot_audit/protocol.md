# Hybrid跨通道slot竞争：是否超出普通符号任务图

2026-10-27；平台固定`19670ba`，研究起点`0e04667`。上一轮已排除默认6/6单转发器轮询顺序的独有性，本轮检查实际分配位置和逆向映射，不重开旧网络/性能实验。

## 机制与竞争解释

固定DeepEP`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`：Hybrid Dispatch在入口GPU的本地workspace上，对目标域内GPU的计数器作atomicAdd；同入口不同通道共享，不同入口使用各自计数器。远端目标GPU的scaleup_buffer按入口的scaleup_rank分区。不能误写为全部入口竞争目标GPU的单一全局计数器。

三种解释事前固定：

1. 交错会改变具体slot，但完整linked-list/元数据逆映射保留后，按原token标识表达的逻辑数据、域内边及返回贡献不变。若成立，普通符号buffer/动态绑定是强基线，不能声称“slot动态所以DAG无法表达”。
2. 去掉位置编号后，原token和返回贡献仍可能改变。保存首个反例，不修输入、弱化基线或宣称仅地址重命名。
3. 即便逻辑相同，实际atomic成本、缓存/内存bank、跨通道共享资源、发布时序与浮点归约可能不同。本轮无这些成本或硬件，不能推导网络完成/性能等价或加速。

## 固定范围与条件

只检查fresh、BF16、无SF的Dispatch分配、copy epilogue的入口分区/linked-list逆映射，以及Combine所需的原token/贡献标识。归约数值、完整Combine执行、所有通知/屏障、GPU弱内存序和网络时刻不在范围。已有单转发器每6项块顺序固定；跨通道外部交错按token步声明，每步对不同目标计数器的操作作为一组。不声称枚举CUDA全部指令交错。

固定8次公开`hybrid-deepep-plan`：

| 输入 | 部署/模型 | 通道 | rank输入 | 固定交错 |
|---|---|---:|---|---|
| synthetic-0 | EP4=2域×2GPU，E8/Top2/H256 | 2 | rank0四token，均选[2,3] | 2+2通道的全部6种 |
| synthetic-1 | 同上 | 3 | rank0六token，均选[2,3] | 2+2+2的全部90种 |
| synthetic-2 | 同上 | 4 | rank0四token，均选[2,3] | 四通道各1的全部24种 |
| synthetic-3 | 同上 | 2 | rank0四token，均选[0,2] | 两目标独立计数器，全部6种 |
| synthetic-4 | 同上 | 2 | rank0四token、rank2两token，均选[2,3] | 两通道各3的全部20种 |
| synthetic-5 | 同上 | 2 | rank0/rank1各两token，均选[2,3] | 各入口2种的笛卡尔积4种 |
| real-c2 | EP16=2域×8GPU，E256/Top8/H7168 | 2 | 15份完整原件按数字序绑定rank0～14，rank15空；entry0/层3各前16原token | 通道正序轮询、逆序轮询、正序逐通道排空 |
| real-c4 | 同上 | 4 | 同一批原token，不复制填充 | 同上3种 |

共150个人工完整逻辑交错、6个真实输入声明交错；人工全枚举累计上限10,000，不能扩展。不同入口独立枚举并作笛卡尔积，不把未枚举的全局时间顺序当成相同。所有输入保留原选择与多专家同GPU去重。只分析目标域0；其他域计划生成但不计为完成。地址大小只按源码TokenLayout/BufferLayout公式，不冒充GPU或RDMA指针。

真实原件复用`runs/hybrid_path_audit_20261027/observed/routes/`的15个固定hash，899/1479缺层排除；来源已由用户确认`cognitivecomputations/DeepSeek-R1-AWQ/mmlu`，远端修订和真实到达时刻未知。六份源码：Hybrid Dispatch、Hybrid Combine、TokenLayout、WorkspaceLayout、Dispatch copy epilogue和host `ep.hpp`；固定tree/blob/SHA准入，只读本地缓存，不下载/执行库。

## 方法和判据

准备只读原件、来源和配置；公开plan置`observed/`。分析仅读observed，生成每通道有序token及符号buffer参考。源码公式解释器置`truth/`执行声明交错，生成实际slot/入口分区/epilogue行号/linked-list和原token数据关联。评价核对两者，不能用truth挑选动作或参考。

保存每条件的全部交错hash、布局hash及规范化逻辑hash；选最早两个不同布局作具体见证。完整前缀、无重叠地址、入口分区、专家去重、元数据逆映射、list终止与全部原token/贡献核对；至少六类证据篡改拒绝。错误共享全目标计数器/省逆映射仅为负控制，不作为收益基线。若有不一致保存首个反例，停止相应等价主张，不改基线迁就。

结果只报告本轮的逻辑是否相同、哪里改变及适用条件，不用布局数量当仿真加速。给出与普通符号DAG、正确分层模型及完整任务图的关系，更新两个创新候选去留。复制源码、空cwd从归档离线重算分析/核验/报告，逐成员hash；不重跑公开plan。

## 预算与停止

根目录`runs/hybrid_slot_audit_20261027/`，累计600受监控秒/256MiB新增分配产物，含30MiB原件、计划、声明回放、归档及恢复；另在其中留2MiB/10秒管理。单命令60秒、RSS1GiB/地址2GiB、保留128MiB空闲。零新网络、构建、GPU、下载/安装、平台或依赖修改/push。先本地提交协议，再执行。失败保留、不提高限额或重跑关闭条件；脚本缺陷只在保留失败后继续未执行部分。总体目标和两项创新均保持进行中。
