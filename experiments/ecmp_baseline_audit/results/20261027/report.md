# 多路径模型的实际边界与有限连接需求核查

新增13份来源已确认，其中11份完整；连同原有4份共有15份完整请求。本轮固定其中8份的同一Decode入口/层，保留batch4/8与四个活动rank。没有新增网络仿真、GPU执行或硬件测量。

原生后端已有固定哈希的等价多路径（ECMP）、拥塞反馈、选择重传和链路故障。旧link_phase预测器要求唯一合规最短路，因此不能直接用于这些多路径图；这是精简预测器的适用范围，不能写成后端或竞品不支持。

| batch | 核心数 | 最多路径数 | Dispatch数据比 | Combine数据比 | 七阶段最大比 |
|---|---:|---:|---:|---:|---:|
| 4 | 1 | 1 | 1.000000 | 1.000000 | 1.000000 |
| 4 | 2 | 2 | 1.333333 | 2.000000 | 2.000000 |
| 4 | 4 | 4 | 2.666667 | 4.000000 | 4.000000 |
| 8 | 1 | 1 | 1.000000 | 1.000000 | 1.000000 |
| 8 | 2 | 2 | 1.333333 | 1.666667 | 2.000000 |
| 8 | 4 | 4 | 2.000000 | 3.333333 | 3.333333 |

比值为确定性连接哈希对应的最大链路帧服务量，除以将同一消息等分到所有最短路径的参考值。只计算MRT1请求和原子回复，不含ACK、CNP、分包头及重传；比值不是时延放大、可消除收益或随机哈希期望。增加核心数也增加总带宽和交换机数量，不能称同成本优化。

| 条件 | 阶段 | 确定性峰值帧服务量 ns | 等分参考 ns | 比值 |
|---|---|---:|---:|---:|
| batch4-cores1 | dispatch.entry | 102.400 | 102.400 | 1.000000 |
| batch4-cores1 | dispatch.counts | 747.520 | 747.520 | 1.000000 |
| batch4-cores1 | dispatch.data | 13916.160 | 13916.160 | 1.000000 |
| batch4-cores1 | dispatch.exit | 102.400 | 102.400 | 1.000000 |
| batch4-cores1 | combine.entry | 102.400 | 102.400 | 1.000000 |
| batch4-cores1 | combine.data | 13854.720 | 13854.720 | 1.000000 |
| batch4-cores1 | combine.exit | 102.400 | 102.400 | 1.000000 |
| batch4-cores2 | dispatch.entry | 89.600 | 51.200 | 1.750000 |
| batch4-cores2 | dispatch.counts | 560.640 | 373.760 | 1.500000 |
| batch4-cores2 | dispatch.data | 9277.440 | 6958.080 | 1.333333 |
| batch4-cores2 | dispatch.exit | 89.600 | 51.200 | 1.750000 |
| batch4-cores2 | combine.entry | 89.600 | 51.200 | 1.750000 |
| batch4-cores2 | combine.data | 13854.720 | 6927.360 | 2.000000 |
| batch4-cores2 | combine.exit | 89.600 | 51.200 | 1.750000 |
| batch4-cores4 | dispatch.entry | 51.200 | 25.600 | 2.000000 |
| batch4-cores4 | dispatch.counts | 373.760 | 186.880 | 2.000000 |
| batch4-cores4 | dispatch.data | 9277.440 | 3479.040 | 2.666667 |
| batch4-cores4 | dispatch.exit | 51.200 | 25.600 | 2.000000 |
| batch4-cores4 | combine.entry | 51.200 | 25.600 | 2.000000 |
| batch4-cores4 | combine.data | 13854.720 | 3463.680 | 4.000000 |
| batch4-cores4 | combine.exit | 51.200 | 25.600 | 2.000000 |
| batch8-cores1 | dispatch.entry | 102.400 | 102.400 | 1.000000 |
| batch8-cores1 | dispatch.counts | 747.520 | 747.520 | 1.000000 |
| batch8-cores1 | dispatch.data | 27832.320 | 27832.320 | 1.000000 |
| batch8-cores1 | dispatch.exit | 102.400 | 102.400 | 1.000000 |
| batch8-cores1 | combine.entry | 102.400 | 102.400 | 1.000000 |
| batch8-cores1 | combine.data | 27709.440 | 27709.440 | 1.000000 |
| batch8-cores1 | combine.exit | 102.400 | 102.400 | 1.000000 |
| batch8-cores2 | dispatch.entry | 76.800 | 51.200 | 1.500000 |
| batch8-cores2 | dispatch.counts | 747.520 | 373.760 | 2.000000 |
| batch8-cores2 | dispatch.data | 18554.880 | 13916.160 | 1.333333 |
| batch8-cores2 | dispatch.exit | 76.800 | 51.200 | 1.500000 |
| batch8-cores2 | combine.entry | 76.800 | 51.200 | 1.500000 |
| batch8-cores2 | combine.data | 23091.200 | 13854.720 | 1.666667 |
| batch8-cores2 | combine.exit | 76.800 | 51.200 | 1.500000 |
| batch8-cores4 | dispatch.entry | 51.200 | 25.600 | 2.000000 |
| batch8-cores4 | dispatch.counts | 373.760 | 186.880 | 2.000000 |
| batch8-cores4 | dispatch.data | 13916.160 | 6958.080 | 2.000000 |
| batch8-cores4 | dispatch.exit | 51.200 | 25.600 | 2.000000 |
| batch8-cores4 | combine.entry | 51.200 | 25.600 | 2.000000 |
| batch8-cores4 | combine.data | 23091.200 | 6927.360 | 3.333333 |
| batch8-cores4 | combine.exit | 51.200 | 25.600 | 2.000000 |

普通哈希模型可由输入重建持久连接的路径需求。不能把相对等分参考的差异直接归于MoE特有机制，也不能把普通ECMP建模包装成论文创新。当前未执行这些多路径图的包级网络，尚不能声称该投影与实际多路径包轨迹等价或会改变配置排序。

源码边界：源QP绑定明确物理NIC；交换机按目的最短跳候选与IP/端口哈希选路。ACK及CNP走实际反向网络，CNP使用既有本地fid投影修复上游未初始化哈希字的缺口。旧反馈保留在同一连接上，路由更新不清除QP速率或序号。live_ecmp只是本地存活候选过滤，不是拥塞感知选路、包喷洒或源端自动换NIC。

阿里缓存版本3e0c7c1也已有ECMP、PFC、ECN和多种QP拥塞控制算法。源码存在不等于本项目全部已接入、校准或能公平比较；本项目现代选择重传入口使用显式Figure6/7 DCQCN参考，不能直接宣称可切换HPCC。

研究去留：①固定连接哈希和共享链路需求应进入通用强基线；下一步若精简模型仍选错配置，才研究哪些反馈/到达/依赖信息不可省。②仿真加速须证明实际包/队列/反馈工作可以减少，并对照优化后的完整流程；不能以省去连接身份造成的错误换速度。这两条目前仍是待验证研究问题。

独立核验通过：True；实际旧连接与原生请求需求核对67个连接、200项远端操作；最短路径枚举795组，双哈希核对2895次。四类篡改均拒绝。

固定平台1c94423；来源清单和源码hash见observed/sources.json，预测不读truth。完整原件与恢复包留runs，Git只保存代码、协议与小摘要。总体研究目标与两项论文创新尚未完成。
