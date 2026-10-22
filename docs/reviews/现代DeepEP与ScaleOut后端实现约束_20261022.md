# 现代DeepEP与Scale-Out后端实现约束

本轮发现会改变研究对照：现代DeepEP已按目的rank去重发送，并批量交换专家计数。旧版的逐专家数据和通知数量不能直接解释现代库的瓶颈。与此同时，现代内核确有跨rank屏障、通信通道与队列资源；不能沿用旧版部分顺序，简单删掉等待后宣称收益。

这是固定源码和真实选择的离线核查，没有运行现代库、GPU或新网络。结果见[源码与摘录](../review_artifacts/deepep_modern_20261022/sources.json)、[逐项核算](../review_artifacts/deepep_modern_20261022/accounting.json)、[验证](../review_artifacts/deepep_modern_20261022/verification.json)。上一轮[v1操作计划](../review_artifacts/deepep_operations_20261022/report.html)的结论保留其明确版本，不改写为现代实现。

## 1. 固定对象和实际适用条件

已把此前仅有README的现代候选固定为DeepEP提交`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`，读取12份源码/说明，核对完整目录树、Git blob、SHA256。两批源码连同目录查询累计响应393,460B，实际请求累计约7.37秒；不是安装、模型下载或依赖升级。该版本README与此前固定blob一致。

这只是用于比较的现代候选，尚未选定为实机校准目标。README要求Hopper或更新GPU、CUDA≥13.1、PyTorch≥2.10、NCCL≥2.32.3，并分别要求节点内NVLink和节点间RDMA。不能把它声明为A100已验证路径，更不能通过修改GPU名称就认为完成校准。实际可用设备信息仍待已有询问的答复。

研究范围暂定为正常无故障、推理解码MoE通信，关心多个专家并行组共享Scale-Out网络时的等待。训练梯度同步、完整模型计算、P/D分离和故障恢复均不是本轮主问题。四份R1-AWQ/MMLU请求用于来源核对，不能代表训练、生产分布或千卡独立请求。

## 2. 同一批真实选择，现代路径做了什么

沿用已核对原件的四请求、两层两步、EP4/E256/Top8、BF16 H7168。下面是新鲜路由、不复用缓存布局、direct模式且所有跨rank目的地均走RDMA的条件核算。它描述所读内核的源级调用，未证明这一四rank部署可以在当前机器运行。

| Dispatch对象 | 逐专家贡献口径 | 现代direct核查口径 |
|---|---:|---:|
| 跨rank数据贡献/目的token份数 | 91个专家贡献 | 38个不同token×目的rank |
| 仅hidden张量的BF16字节 | 1,304,576B | 544,768B |
| 专家计数交换 | 旧v1为3,072个跨rank原子count调用 | 48个跨rank专家计数块put，另有48个rank计数put_value |

direct Dispatch会将同一token指向同一rank的选择去重，只传一份含路由信息的token；专家展开在其他阶段完成。这里数据调用数恰与原先38条Dispatch抽象rank-pair消息数相同，是每rank仅一个token造成的；原抽象仍按专家贡献累加字节，不能因此说两种模型等价。batch增大后，一个rank-pair可能包含许多独立token put。

38份hidden张量是数据内容核算，不是544,768B线上流量。现代TokenLayout还含top-k索引、权重、来源和对齐；没有把旧版16B元数据移植过来，也没有在未核查对齐常量时补一个假包长。

现代计数分为rank与expert两部分：每个跨rank目的地一个int64 rank值和一个本rank拥有的64个int64专家计数块。四调用共96个跨rank调用，操作数字节为24,960B。旧v1的3,072次count对应12,288B操作数，调用更少不等于操作数字节更少，更不等于包数或时延更小。GIN还可能聚合请求；屏障、响应和其他控制操作均未包含在此计数中。零专家计数被放进块中，没有在语义上消失。

两种独立枚举均得到38个目的token份数；16条选择逐条对照原始四文件，没有从随机分布生成热点。这里只核算选定Dispatch路径，不把Combine强行套用同一公式：现代Combine有本地归约和expanded-send等不同分支。

## 3. 三个必须更正的建模假设

第一，现代QP索引与旧版专家key不同。`get_qp_mode`按SM、warp通信通道和通知角色分配GIN上下文，通知使用上下文0，数据可在多个通道间共享。DeepEP源码还明确留下“QP索引由谁映射”的TODO。因此不能把源码中的`num_qps`直接当每peer的物理RC数量，也不能把旧版`[src,dst,local_expert]`机械复用到新版。物理连接、NIC、QP与上下文的映射仍须核对固定NCCL provider。

第二，现代direct确有屏障。所读Dispatch和Combine各有入口、出口两次gpu_barrier：入口用于远端buffer可用，出口用于数据到达。实现会在需要release时flush各上下文，并以QP索引0发送信号、等待所有rank的滚动计数器。四调用的这两个核心内核合计16次组屏障、192次跨rank signal API调用；这不是完整host/epilogue/NCCL操作清单。没有依据将这些屏障删掉，也不能把旧版无额外组返回屏障的计划推广到新版。

第三，源码字段名不能直接对应物理拓扑。关闭hybrid时，Context把逻辑`num_scaleout_ranks`设为1、`num_scaleup_ranks`设为全组大小，但物理`num_rdma_ranks`可以大于1，`is_scaleup_nvlink`也可以为false。看到“scaleout=1”不能判断没有跨节点流量。

## 4. Multi-rail、Clos和HPN应怎样接入

现代hybrid不是“给direct多分配几条网线”：它按rail团队把token送到远端Scale-Out域，之后通过NVLink转发到目标GPU。其Dispatch先按目的Scale-Out域去重，再在域内按目标GPU分发，控制进度由通道tail和finish标志推进。Combine也有对应混合路径，但本轮没有完整建模其全部分支。

Context对direct要求FULL连接，对hybrid要求RAIL连接，并检查GIN能力。某个multi-plane网络可能只允许rail内互通；若direct全连接能力不存在，不能让仿真器默默跨plane转发，再将这个不可部署的基线与hybrid比较。DeepEP自己的报错已提示这种网络配置限制。

| 必须分别声明的对象 | 内容 | 验收条件 |
|---|---|---|
| 物理网络 | GPU/NIC、NVLink域、rail、ToR/汇聚/核心、各层端口/带宽/时延 | 按实际端口图验证可达性；节点内转发消耗对应链路资源 |
| 运行时逻辑团队 | WORLD/LSA/RAIL与物理rank的对应 | 每个运行时目标都能映射到声明的物理端点；不凭字段名推断节点位置 |
| 连接和通道 | GIN上下文、物理连接、拥塞状态、NIC队列 | 固定provider后核对映射；逻辑索引不是端口或ECMP hash |
| 通信算法 | direct/hybrid、rank/domain去重、通知块、归约模式 | 同一输入分别生成正确操作；不把算法减少的数据混算为拓扑收益 |
| 选路 | rail/NIC选择和交换机逐跳路径 | 先选可达的端点/rail，再由后端执行选路；原始记录不能反向控制选路 |

Clos/汇聚/HPN式可配置拓扑仍有价值，但仅画出千卡图不够。需要在相同总端口/链路资源、相同放置和可部署算法下比较共享链路排队。数据去重、NVLink转发、端点选择与拓扑超售同时变化时，必须拆开解释。千卡总集群也不意味着单EP组必须跨千卡：本快照hybrid Dispatch显式要求`kNumScaleoutRanks<=32`，这只是该路径的域数限制，不是整个集群或整个库只能32GPU；超出需分组或选择有依据的其他路径，不能自动复制四请求补齐。

## 5. 后端最小改法及验证顺序

当前平台的原生RDMA入口仍是rank聚合字节消息，离线DeepEP计划仍是固定v1；下面是待实现设计，不能当成已可调用接口。两者保留原行为和版本声明。

| 顺序与目标 | 实施方法 | 通过条件 |
|---|---|---|
| 1. 固定可校准的目标 | 对照设备选择运行时提交、NCCL版本/provider、精度与direct/hybrid；建立物理域和逻辑团队映射 | 不支持的设备、团队或连接模式明确拒绝；不伪装成已校准 |
| 2. 形成现代应用操作 | 分开数据put、put_value、批量计数、signal、flush/wait和消费依赖；显式存buffer、目标偏移与epoch | 同rank多专家只发一份token；零计数块仍交换；入口/出口屏障均保留；与当前v1接口独立 |
| 3. 分离连接和工作 | 持久连接包含拥塞/序号状态，可追加工作条目；只有实际就绪条目进入队列，连接空闲不等于关闭 | 同一共享网络时钟；延后就绪工作不提前发包；每条工作恰好一次完成；资源满时真实反压 |
| 4. 实现选定provider操作 | 根据固定provider明确put/信号的事务、响应、顺序与重试；分别上报接收接受、GPU可见、发送完成和缓冲可复用 | 重传不能重复应用原子/信号；延迟ACK不能提前释放源buffer；flush必须覆盖相关在途工作 |
| 5. 验证网络与端点 | 独立记录NIC提交等待、链路队列、数据/控制/响应包、NVLink转发、消费者等待与网络排空 | 记录不改变路径/优先级；累计等待不重复计时；目标可见不被自动等同ACK或kernel完成 |
| 6. 匹配校准与正常基线 | 从小型可测的单链路/双流开始，校准对应设备与provider，再做真实路由多EP组共享网络 | 留出配置预测通过后再评价优化；与现代去重/批量通知、简单调度及已有系统比较 |

第3、4项需要当前锁定ns-3未表达的能力，不能用改端口号或向现有Send注入4B控制流替代。依赖修改须在明确范围下保存独立分支和gitlink；本轮没有改锁定依赖，也没有偷偷改变旧实验的通信定义。

## 6. 对论文目标的影响

本轮否定了一个过弱方向：把旧版逐专家通知简单批量化，不能直接作为现代DeepEP上的创新。现代代码已批量交换计数并去重token，因此后续须证明在这些机制已经存在时，共享Scale-Out网络中仍有什么显著且可解决的业务损失。

保留的主问题是：真实非均匀路由、多EP组、受约束的rail连接和有限端点资源共同存在时，现有方法是否能正确识别并缓解关键等待；如果简单基线已足够，就应如实收尾该候选。源码差异、操作计数和可复现工具是研究基础，尚未构成A类论文贡献。

可继续独立完成的是现代操作依赖规范与provider资料核查；硬件匹配、留出请求和千卡长工作量执行证据仍缺失。当前没有速度收益、现代库可执行性或最佳论文承诺。

## 7. 复核入口

```bash
python3 experiments/paper_readiness/audit_deepep_modern.py \
  --source-dir data/research/deepep_modern_20261022 \
  --output docs/review_artifacts/my-modern-review \
  --checkpoint runs/my-modern-checkpoint
```

源目录和四原件从[现代源码检查点](../review_artifacts/deepep_modern_20261022/archive.json)与上一轮操作检查点恢复；不安装库、不运行GPU。公开固定版本源码也可通过`collect_deepep_modern.py --phase tree --commit f6aa38fe06cd4a0489e7730365d4e0114970a2f3`加新输出目录获取，文件清单分两批列于[采集记录](../review_artifacts/deepep_modern_20261022/collection.json)，每批最多6份、最多3并发，共12份且累计响应≤16MiB/请求时间≤120秒，不自动重试。源码核算结果不能转称包级仿真通过。HTML已检查本地链接，没有浏览器视觉验收。
