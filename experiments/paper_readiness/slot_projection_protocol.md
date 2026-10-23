# 并发槽位与静态连接需求：执行前方法

从干净`ee1f3c9`继续。上一轮已完成网络/本地阶段平台增量，属于实质进展。本轮回到研究，判别现代direct布局中哪些网络需求能够消去未观测的槽位顺序。不是现代端到端性能、GPU校准或新的调度算法实验。

固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`，只读已有dispatch/combine/barrier/token及launcher源码，按已有Git tree/blob核对，不联网、不执行外部源码。使用当前公开现代计划及事件API/CLI，平台和依赖不改、不构建、不安装。全部回调是调用者声明的无时间事件，不是网络或GPU观测。

待证伪命题：在fresh direct、BF16、expanded layout且本地多专家归约、固定hidden/top-k/团队/上下文映射的现有范围内，source→owner的token去重计数为n。其接收序号占连续区间[p,p+n)。Combine按owner的总接收数N将该区间与每warp的连续区间求交；因此任何合法槽位双射下，每owner→source/context的token put数量和操作数字节保持不变，但token身份、目标地址与依赖不必相同。必须保留本地旁路token参与N和prefix，不能先排除它们再分配通道。

先检查原始Combine连续分段、Dispatch按源/目的counter分槽、get_qp_mode和token布局，若条件与命题不符则记录反例停止，不修改范围挽救。静态投影只计算操作数，不等同WQE、包、线上字节、吞吐或完成时间。

固定两组输入：

- 人工4 rank/8专家/Top2/H256，source0的4个token均选择专家4/5，其余rank无token。2SM、Dispatch/Combine各2warp、call contexts2/requested17、2 connections，设备号仅为声明值。4个token分别在不同Dispatch通道，枚举全部4!=24个槽位分配顺序，完整公开事件执行；独立验证它们均合法并覆盖全部双射。检查静态连接需求相同，同时主动寻找同一个token落在不同连接的见证，防止把无差异输入当作证明。
- 四份已见R1-AWQ/MMLU请求，entry0/层4，每rank截取至多16 token，保持平台16-token上限，预期长度16/16/10/6。8SM、Dispatch/Combine各4warp、call contexts8/requested17、2 connections，设备号仅为声明值，不称现代默认有效硬件配置。执行三种固定合法就绪策略：slot字典序正向、反向、SHA256排序；其他可用事件字典序。保持相同输入和所有完成义务，只改变合法事件顺序。不是独立请求、完整Prefill或真实调用时序。

准备从原件产生observed/input和公开计划；truth保存原件身份及选择，不供分析读取。执行通过公开ModernDeepEPRuntime生成声明transcript，公开modern-deepep-replay校验。分析只读observed、公开plan与执行log；用区间交集公式产生静态需求，并与所有issued记录的已解析映射对照。独立checker不得导入主投影函数或平台内部生成器：回查原件、直接枚举receive index与warp循环，检查每个slot双射、每条发行记录、同通道顺序和所有事件前驱，以及24个全排列与token身份变化见证。重复分析和换源码根/cwd重跑小型无时间流程，结果/声明transcript可复核，宿主资源字段不要求相同。

区分四种结论：若连接需求改变则命题不成立；若仅身份改变而总量相同则证明所述静态量可约简；若没有不同身份见证则输入区分力不足；即使全通过，也不能证明时间轨迹/包级等价或足够支撑论文。明确进一步反例：变长token、按token变化的本地处理/就绪、不同归约/布局或跨调用buffer寿命不能由此定理消去。

每命令60秒/RSS1GiB/地址2GiB/阶段32MiB，累计受监控240秒/128MiB；归档成员48MiB/压缩16MiB，内存盘预留8GiB。原始件只放`/dev/shm/moe-slot-projection-20261023/`，易失无持久备份，根卷不足8GiB只保存小报告/源码。不运行网络/GPU、不改旧结果、不重跑已失败Prefill/千卡、不扩大现代输入上限、不用新的参数矩阵寻收益。失败保留，不自动抬限或重试。交付研究程序、独立核验、中文HTML和本地提交，不push；论文主贡献未成立则目标保持进行中。
