# 现代 GIN 自发请求与千卡连接规模

本轮解决的是接入前一个具体问题：GPU“发给自己”的通信，后端究竟该保留哪些连接资源。固定源码表明，它使用主 QP 和独立 responder QP 互相连接，不能删掉，也不能按普通跨机器传输直接套用网络成本。本轮没有运行新的网络仿真，没有获得性能提升结果。

## 本轮做成了什么

固定 NCCL v2.32.3-1 和现代 DeepEP 提交，离线核对四份源码的 Git blob 与 SHA256。提取六段原文，包括信息填充、MTU 检查、QP 建连、两段自发建连循环及资源声明。在 CPU 上执行未改写片段，外部 API 由记录参数的替身实现；没有调用 NCCL/DOCA 运行时、GPU 或网卡，替身类型和常量也不是厂商 ABI。

独立检查覆盖 39 组自发配置、78 对主/响应 QP、483 次成功建连状态修改、25 种合法 MTU 组合、11 类建连对照、空指针跳过和 30 组资源声明。483 包括自发路径的 468 次和五个成功对照的 15 次；空指针对照另外检查，不混入这一数字。对照包括远端 LAG、IB 地址分支、原子额度覆盖、非法 MTU、ordering 不匹配，以及 INIT/RTR/RTS 各阶段失败后的清理。

## 对网络建模有用的结果

| 问题 | 固定源码及本轮执行能确定什么 | 还不能确定什么 |
|---|---|---|
| 自发请求连接谁 | 每 context 的 self 主 QP 连接独立 responder；responder 反向连回该主 QP，两个 QPN 不同 | NIC 内部怎样回环、是否经过外部交换机 |
| 自发请求是否继承远端选口 | self 两个循环不传 LAG 参数，默认值为 0；远端分支单独传 context 对应的亲和值 | 0 对应的实际硬件选路与拥塞代价 |
| 建连状态 | 两个对象均经历 INIT、RTR、RTS；保留地址、MTU、PSN、读/原子额度等属性 | 实际报文格式、重试性能、GPU 可见性 |
| responder 的门铃模式 | 创建 responder 列表前，源码显式设置 VALID_DBR；本项为静态摘录 | 硬件门铃耗时，以及本机可部署性 |
| 计数伴随 QP | DeepEP 此路径保留初始化默认 ginCounterCount=0，因此本配置不启用 companion QP | 其他应用或配置不能沿用无 companion 结论 |

函数中没有显式设置“经过交换机的回环路径”，不代表请求一定不出网。记录型替身不能提供这样的硬件证据。两个资源对象也不代表一次应用请求应被重复注入两遍。

## 千卡场景必须分清两个规模

集群有 1024 张卡，与一组专家并行（EP）有 1024 张卡，是不同配置。本轮只估算 GIN 连接对象：选择 FULL 连接、请求 17 个 context、2 个 GIN connection、无 companion QP。NCCL 将 context 向上取整为 18，每个 connection 分到 9 个。这里 connection 是 provider 连接实例，不能自动等同两个独立物理 NIC 或 rail。

设一组 EP 有 R 个 rank、总共分配 A 个 context，每 GPU 的跨 rank 主 QP 为 A×(R−1)，self 主 QP 和 responder 各 A 个，总数 A×(R+1)。计数包含持久分配的全部 peer 对象，不代表它们都有当前流量。

| 集群卡数 | 每组 EP 卡数 | EP 组数 | 每 GPU QP 数 | 集群 QP 总数 |
|---|---|---|---|---|
| 1024 | 4 | 256 | 90 | 92,160 |
| 1024 | 32 | 32 | 594 | 608,256 |
| 1024 | 128 | 8 | 2,322 | 2,377,728 |
| 1024 | 1024 | 1 | 18,450 | 18,892,800 |

这张表是明确假设下的对象数投影，后两种也不代表现代前端已支持相应规模。没有实例化千卡网络，没有测量宿主内存、QP 硬件容量或执行耗时。独立 EP 组数也不能直接标成数据并行（DP）数：还需指定 TP/PP 等并行方式、rank 映射和 DP 通信；本表未计 DP AllReduce、其他通信库或服务副本资源。

对接下来的规模实验，先比较“相同 EP 组、增加共享网络中的组数”，再分别研究“固定集群、扩大 EP 组”。前者检查跨组共享链路干扰，后者同时改变通信参与者和连接资源；不能把二者混成一个卡数旋钮。是否需要按需实例化仿真对象，应由实际内存/调度开销测量决定，不能提前删除无流量连接的必要语义状态。

## 后端接入如何改

1. 明确使用 connection/context/peer 的持久身份，区分 self 主 QP 与 responder。responder 是资源角色，不是新增应用操作；一次请求仍只发起一次。
2. 保留 self 请求的提交、源安全和目标可见事件；物理路径及成本缺证时显式不支持该完整执行，不能用零时延事件或任意交换机路径补齐。当前 MRT1 只是一条功能载体，不能直接作为 GIN 成本基线。
3. 将集群卡数、EP 组大小、通信组到 GPU/NIC 的映射分别作为实验输入；检查 FULL/RAIL 可达性。FULL 的资源公式不能套到 RAIL。
4. 完整现代性能比较仍需实际窗口、provider 发布/响应、目标可见、源复用与上层事件共享时钟，再用匹配硬件校准。本轮没有完成这些工作。

## 证据与复现

[独立核验与规模表](../review_artifacts/self_peer_provider_20261023/verification.json)、[源码身份](../review_artifacts/self_peer_provider_20261023/sources.json)、[原文提取位置](../review_artifacts/self_peer_provider_20261023/excerpts.json)、[恢复检查](../review_artifacts/self_peer_provider_20261023/recovery.json)和[归档清单](../review_artifacts/self_peer_provider_20261023/archive.json)可分别复核。[实验方法](../../experiments/paper_readiness/self_peer_provider_protocol.md)记录边界和预算，[复现入口](../../experiments/paper_readiness/README.md#self-peer-provider-源码执行核查)说明公开命令。

原始件和检查点位于 `/dev/shm/moe-self-peer-provider-20261023/`，属于易失存储，无持久备份。仓库保存必要小型摘要与 HTML；原始源码另可按固定版本与 blob 校验重新取得。本轮推进的是模型可信度和规模实验定义，论文的主贡献与性能证据仍未建立。
