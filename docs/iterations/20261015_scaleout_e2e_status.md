# 当前任务与交接

状态：`completed`（本次小规模Scale-Out基础实验）。目标：使用Clos/Agg、多rail/plane等已讨论拓扑，多尝试基础条件，打通LLMServingSim→ASTRA→非UB ns-3→下一轮调度，并交付可视化。全部修改留在 `/home/lry/moe-ub-cosim`；平台提交 `3897d82`，实验独立本地提交，**不push**。

## 完成范围和直接证据

| 验收项 | 实际结果与证据 |
|---|---|
| G0：独立原生网络/协议 | 固定ASTRA ns-3 3.42构建成功；每程序只链接一套非UB库。分包边界、incast、NACK与尾包失败均保留，[协议证据](../review_artifacts/scaleout_basic_20261015/protocol_evidence.json) |
| G1：真实工作图进入网络 | 实际Scheduler组批，真实Chakra文件送ASTRA；逐token路由重算远程payload，SEND只发一次、RECV只等待。公共[RDMA入口](../../examples/scaleout_rdma/README.md) |
| G2：事件驱动的多层多轮 | 2层4次Decode，接收/ACK分别推进依赖；完成后add_done再schedule，最终请求FINISHED且KV释放。31次主/rail结果的42712节点逐前驱和回调核验，[独立验证](../../experiments/scaleout_e2e/evidence/validation.json) |
| 共享网络 | 跨Pod交错EP4在同一原生进程/网络运行，8波次、358消息、972节点通过；两组实际共用4个Agg出口，[共享证据](../../experiments/scaleout_e2e/evidence/shared_fabric.json) |
| G3：基础网络对照 | 10条件×seed11/23/37，共30次服务；另独立rail合法EP4和非法EP8预拒绝。覆盖拓扑、batch、上联带宽、plane、热点和分组，[结果说明](../../experiments/scaleout_e2e/conclusions.md) |
| 可修改、复现 | 模型变形H512/F2048/E8运行通过；基线result及36个图文件逐字节复现；3项篡改证据负对照被拒绝；[执行入口](../../experiments/scaleout_e2e/README.md) |
| 可视化 | 7组实际数据图，保留PNG/矢量PDF、CSV/JSON、[离线网页](../review_artifacts/scaleout_basic_20261015/report.html)；逐图视觉检查 |
| 相关回归 | 28项Scale-Out单元检查、完整模型导出兼容检查4个小型case、13项固定依赖检查通过，[执行记录](../../experiments/scaleout_e2e/evidence/regression_commands.json) |
| 方法PDF | [8页v0.2审阅稿](../review_artifacts/ScaleOut实验方法_审阅稿_v0.2_20261015.pdf)保留方法、对照与限制；A4、嵌入中文字体、可搜索，v0.1是历史方案 |

## 版本、资源和原始证据

主矩阵于实施工作树运行；随后零成本COMP修复不适用于主矩阵（其零时长COMP数为0），最终后端的基线独立重跑一致。源码与二进制hash、overlay、当时HEAD和最终平台提交的关系见[版本记录](../../experiments/scaleout_e2e/evidence/versions.json)，不以单个旧HEAD替代实际源码版本。

构建限300秒/并发2/RSS2 GiB/地址空间4 GiB；网络单例限宿主60秒/RSS1 GiB/地址空间2 GiB/模拟5 ms。专用临时卷 `/dev/shm/moe-scaleout-e2e-20261015/` 累计512 MiB、预留8 GiB，未自动扩限。主矩阵87.519秒、采样峰值RSS688615424 B；资源记录位于实验evidence目录。根盘预留不足时没有继续在根盘构建，改用已核实独立且可执行的内存盘；不删除未知文件。

完整原始输入、Chakra/ASTRA日志、结果、资源和失败记录已持久备份到 `runs/scaleout_e2e_20261015/evidence.tar.gz`，约20.2 MB，3019个成员逐文件hash校验通过；[归档摘要](../../experiments/scaleout_e2e/evidence/archive.json)。构建对象和二进制不提交；另一台机器通过公开CLI重建。内存盘不是持久备份。

## 明确的适用范围

仅有限ready-KV推理MoE片段、合成专家路由、声明计算成本；A100是GPU标签，切换标签不会自动校准。全部组/层/轮次共享一个网络，不混链UB，不拼接独立时延。尾包丢失无通用自动恢复，故服务实验限定无故障。主EP4混合放置和副本变化；双plane总带宽固定但设备数量不同；3个路由seed不支持生产p99。

千卡静态拓扑已在此前阶段交付，本轮没有执行千卡逐包；真实HF原始路由依用户“先做别的”暂缓，不属于本轮合成实验完成条件。完整训练/DP同步、完整模型/Prefill、GPU校准、NVLink/PCIe详细模型和完整HPN协议仍需独立任务，不自动启动。

## 历史入口

此前代码审阅、库研究、拓扑及规模研究见[代码审阅](../reviews/scaleout_review_20261015.md)、[ns-3/Alibaba/ASTRA源码研究](../reviews/scaleout_ns3_backends_20261015.md)、[拓扑迭代](../iterations/20261015_scaleout_topology.md)、[规模/热点迭代](../iterations/20261015_scaleout_scaling.md)。其中“尚未实现/尚未装载”属于各次提交时状态，当前独立RDMA小图能力以上表为准。上轮Scale-Up筛选见[原结论](../../experiments/scaleup_issue_screening/conclusions.md)，不重启后续算法工作。
