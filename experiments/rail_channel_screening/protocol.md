# 双端口数据传输筛选：执行前方法

从干净`4d63d21`开始。上一轮self-peer源码执行是实质进展；本轮判别一个网络候选：现代direct内核固定token序号到context的分工，是否使按发送量调整token顺序值得继续研究。不是新算法或完整现代库性能基线。

来源固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`、NCCL `12df1a11afad322be5a204a2db890161cbf8131d`，仅复用已验证缓存。数据为四份已见R1-AWQ/MMLU请求的entry0/层4，每rank取最多16个token，预计16/16/10/6；这是不等长Prefill前缀，不能称独立请求留出、相同batch16、完整Prefill或训练。另核算每rank首token但不重复执行与顺序无关的相同网络。

固定H7168/Top8/BF16、256专家连续映射到EP4，direct数据按owner去重，每远端token份14,464B。声明8SM/64KiB可用共享内存，从固定launcher推导4 Dispatch warp，调用8 contexts、申请17/2 connections分配18；这些是条件输入，未读回Hopper配置。数据context到connection为index%2。这里显式声明connection0/1绑定物理GPU端口1/2；不能当作真实NCCL选NIC结果。

三种策略预先固定：identity（原顺序）；rotate（一号rank循环移一位、二号移两位等，不看路由）；local_lpt（只读当前rank已知路由，按远端去重目的数降序，将token放入剩余槽中使该rank两端口累计发送份数的最大值最小，次序按平方和和槽号决胜）。LPT为经典负载均衡对照，不主张创新；不使用其他rank路由或未来entry。每个token占一个现有序号，固定每rank槽数、布局、目的地和份数；实际使用还需付出重排/恢复成本，本轮不计，不能据此宣称端到端收益。

一次数据传输微基准中，全部数据在起点已就绪，全部工作为无依赖根；这是显式的原生字节传输问题，不是从现代DAG删屏障后冒充应用执行。不模拟self GIN、计数、signal、GPU本地处理、源/目标可见、门铃或跨QP发起成本。不使用该结果填补现代完整接入缺项。

物理图为4GPU各独立server，2pod×2server×1GPU、1rail×2plane，plane互不连接；每GPU一个声明双端口NIC，总预算200Gb/s、每端口100Gb/s。每pod每plane一个ToR/Agg、每plane一个Core，唯一最短路径，避开ECMP变化。两种固定上层速率100/25Gb/s；访问速率均100Gb/s、逐跳1us。6次网络条件=3策略×2带宽。分开统计本模型plane与rail，不称两独立NIC。

每条件都创建相同96条持久data QP（4×3peer×8call context），包括无数据的对象；固定wire_port为20000+stream_id、深度32，工作远少于额度，理论不产生槽等待。工作全部根就绪且每token份单独发布，不聚合成大流。只有真实接收/ACK推进accepted/acked和排空；6条件各自在一套固定原生ns-3里完整执行，彼此不拼接。使用已有已核hash二进制，不改平台/依赖、不构建、下载或运行GPU。无硬件校准，不将carrier帧字节当RoCE线速成本。

竞争解释：源端份额改善；目的端热点未变或更坏；上层共享链路服务量主导；报文拆分/QP选择/相对发送次序变化。预先核对每token远端集合、消息/包数、单包长、总操作数字节和所有物理端口服务量；分别给出源/目的/最忙物理有向端口的串行服务下界（不相加为时延），及实际最后接收/ACK/排空时间。不能从NIC下界降低直接推出完成更快；无改善、排序翻转、微小差异均正常保留。若只有零差异或经典均衡无稳定网络优势，就不继续把本候选当论文主创新；不追加带宽/seed/阈值寻找收益。

准备程序独占原件并生成observed/truth；策略和分析只读observed输入/执行结果。独立checker回查原件、逐token/warp枚举context，独立枚举全部源包并累加每个实际包的端口序列，核对两平面绑定、持久序号、字节守恒、真实回调和排空；不导入平台内部期望或测试fixture。实验通过公开topology/rdma-stream-probe命令，不能导入tests或历史资源测量脚本。

预算：原始目录`/dev/shm/moe-rail-channel-screening-20261023/`。每命令60秒/RSS1GiB/地址2GiB/产物64MiB，仿真5ms/单结果16MiB，6条件网络合计受监控≤360秒；全阶段≤480秒/192MiB，归档成员64MiB/压缩24MiB，卷预留8GiB。失败保留、不自动扩限或重试，不重跑旧失败1024/完整Prefill。中文HTML、复现工具、核验与本地提交，不push。若没有新增论文贡献则研究目标仍进行中。
