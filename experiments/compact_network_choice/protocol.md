# 新请求与较大batch：通信表示对网络选择的影响

2026-10-27，起点`6aa55ff`。独立研究实验，不改平台。判断精简表示是否已足以选择网络，不能将完整MoE控制器或总字节基线的不足当作创新。

## 固定输入

用户确认新增文件均来自DeepSeek-R1-AWQ/mmlu。按接收清单本地SHA核验，新文件远端修订/blob未独立核实，不生成冒充已核实的来源manifest。整份排除缺层的899/1479，不补齐或复制请求。按编号选900、901、905、1003、1004、1007、1008、1011；batch4用前四份，batch8用全部八份。取数组下标1（首个Decode入口）、层3，每请求一token；序号模4分配源rank，连续64专家/rank，EP4/E256/Top8/H7168/BF16。

仍只有4个活动rank，不是8卡或完整服务。样本由用户选取，不是随机或严格留出；两batch内容组合不同，不将差值全部归因于batch。原公共示例保留完整计数/屏障/self/动态slot与channel/源保护、选择重传、DCQCN参考及声明成本；2SM/2warp/5context/2connection、双NIC/plane，节点0/1/4/5。max_tokens_per_rank分别1/2，仅匹配容量。

## 精简基线：预测先于新网络冻结

通过公开modern-deepep-plan编译符号计划。预测器只读observed/中的计划、现代输入、拓扑配置及本地来源，不读truth/、回调、队列或未来结果，不拟合常数。

1. volume：按Dispatch/Combine两阶段、源/目的rank聚合token operand字节，各阶段最大端点服务量除声明总NIC速率。仅为简单参考，不能作为唯一竞品。
2. phase_payload：七个网络阶段（Dispatch入口/计数/数据/出口、Combine入口/数据/出口）的端点operand矩阵；每阶段取max(最大端点服务量+最远活动rank对最短单向传播,self/local服务)，再加声明本地准备/归约/专家成本。计数/屏障保留。参考拓扑同pod同ToR为2跳、跨pod为6跳，两个plane同构，不用真实包路径反推。
3. phase_framed：沿用2，额外计每远端请求32B MRT1头，fetch_add另计40B返回帧；这是MRT1帧服务量，不冒充线上字节。不添加未经核实的RoCE/ACK头常数。

两个phase方法为普通分阶段消息/传播/服务模型，没有细粒度依赖、队列、QP反馈或动态通道。不是严格上下界或精确时延模型。记录表示大小/聚合单元数，不把离线表示缩小当作端到端模拟加速；合理精简基线已选对时不能将作用归给完整MoE语义。

## 完整强基线与停止条件

每batch依次baseline（100G/1000ns）、bandwidth（200G/1000ns）、propagation（100G/500ns）。带宽档同步调整NIC总速率和ECN速率键，其余资源/阈值不变。是参数敏感性，不是同成本软件收益或商业ROI。

每条件先运行公开modern-deepep-native --callback-batches。只从该batch的baseline提取并冻结已有通用事件图/native工作，不含绝对时刻；离线提交/事件重放等价后，通过公开NativeTransactionSession执行一次通用图。待测网络不得参与图构造、slot/channel调整或成本拟合。

按已有稳定身份归一任务编号：count_prepare用call/rank，其余用role/event。允许双射改名，禁止按时间/包拟合。逐项比较工作及依赖、应用事件/回调/注册/提交时间、事务/内存/本地工作、包/队列、应用完成/源释放/排空。冻结绑定不适用则保留反例，不泛称DAG无法表达MoE。

至多12次网络：batch4三对，然后batch8三对，各一次。现代未完成、捕获遗漏、图离线/在线真实不等价或任一预算失败即全局停止；不加限、重跑成功网络、扩条件或改图保结果。稳定编号改名按事前规则处理。

每batch评价三个网络的应用完成排序、最优配置集合、后悔值(T_selected-T_best)/T_best及绝对误差。预测并列按baseline/bandwidth/propagation固定次序选择，同时报告全部并列项。另报告全部事件/源保护释放和物理排空；不能当实测用户请求时延。精简法选对不证明新表示收益，全图失配不自动证明精简法有益。

## 预算、验证与交付

新目录/dev/shm/moe-compact-choice-20261027/，持久runs/compact_choice_20261027/。运行时从modern_selective_checkpoint_20261026归档按static_dag_transfer/prepare.py固定SHA和成员manifest核验恢复，再核程序/库/原生源码身份；不信任未经验证的tmpfs运行时。

累计1200受监控秒/1GiB（输入副本、运行时、结果、归档、恢复副本及2MiB收据预留）。单命令60秒/RSS1GiB/地址2GiB；每网络1ms/result16MiB/capture20000，现代256物理交互/16384逻辑步及其他默认限制不变；tmpfs预留8GiB。逐次检查剩余额度，保留失败，不加限。零下载/安装/构建/GPU/平台或依赖修改/push。

检查人工阶段/字节/路径/并列选择；独立从八原件重数操作覆盖；拒绝来源hash、阶段、完成和捕获遗漏篡改；移除truth后预测逐字节一致；复制源码/空cwd从逐成员核hash归档离线恢复分析。代码、配置、协议和小摘要入Git，原始结果持久保存，实际命令/资源留在运行目录。两个创新候选仍待验证。
