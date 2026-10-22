# 现代DeepEP direct操作计划

用途是检查现代MoE通信所需的数据、计数、屏障及动态接收槽依赖。输出是固定源码分支的无时间操作部分顺序；不执行CUDA、数值专家、WQE队列或包级网络，也不预测性能。与[v1低延迟计划](../deepep_partial_order/README.md)采用不同schema和命令，二者不能互换。

从仓库根运行，不需要前端依赖、旧结果、GPU或编译后端：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout modern-deepep-plan \
  --config examples/modern_deepep_partial_order/input.json --output runs/my-modern-plan
PYTHONPATH=python python3 -m moe_ub_cosim scaleout modern-deepep-check \
  --plan runs/my-modern-plan --output runs/my-modern-check.json
```

CLI路径相对当前工作目录；输出目录/文件必须不存在，父目录必须已存在。输入不含外部路径。三个输出是原样输入`input.json`、规范化`plan.json`和hash/数量清单`manifest.json`，可以整体复制后检查。重复编译相同输入应逐字节一致；检查会重新编译核对表示，这不是独立源码或硬件验证。

## 支持范围和输入

固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`及NCCL `12df1a11afad322be5a204a2db890161cbf8131d`。选择fresh direct、跨rank全部RDMA、FULL连接能力由使用者声明、BF16、expanded layout、本地多专家归约、expert alignment=1、计算流等待且不推迟epilogue。所有跨rank端点可达及Hopper+等库/设备条件未由本命令验证。hybrid、缓存路由、FP8、屏蔽专家和其他归约分支明确拒绝。

| 字段 | 含义与当前检查上限 |
|---|---|
| `model` | E2—2048、Top-K1—32且不大于E、H256—8192且整除256、precision=bf16 |
| `execution.ranks/max_tokens_per_rank` | 2—32 rank，每rank容量1—16 token；E整除rank，每rank专家≤512 |
| `num_sms/dispatch_warps/combine_warps` | 各1—8；Dispatch额外采用4个notify warp；是声明的模板角色数，不是宿主或实测GPU资源 |
| `call_contexts/requested_contexts` | 各1—64，前者≤后者；实际分配向connections的整数倍取整，调用仅用call_contexts |
| `connections/local_devices` | 1—4条GIN连接，每rank显式提供1—4个本地设备编号；连接可落同一设备；不是拓扑探测 |
| `calls` | 1—4次顺序的Dispatch—专家—Combine，name唯一；tokens按rank列出每token的不同专家索引，可有无本地token的rank |
| `limits` | events≤65,536、operations≤16,384、input≤1MiB、总output≤16MiB；示例设置更小输出预算 |

这些上限是有界功能范围，不是通信库或千卡集群上限。示例为人工选择，既不代表训练分布，也不是完整模型部署。输出卷需要预留8GiB。批次、选择、H/E/K、声明通道或设备表变化时重新编译；不自动扩限。

## 怎样解释输出

每个`operations`记录包含数据或控制操作、源/目标rank、操作数字节、逻辑region/epoch/偏移、上下文绑定及issued/accepted/visible/local_complete事件。四种事件是未来后端的语义义务，不是已测时间点；local_complete不自动等同远端GPU可见。barrier signal的8B是信号值宽度，不能当线上包长。

Dispatch按目的rank去重，每token/目的只传一份含metadata和对齐的布局；另交换编码为`-count-1`的rank计数及专家计数块，包括零值。计数和数据角色可以并行；输入中不能预先填通信发起时刻。入口/出口屏障、flush覆盖、epilogue、专家阶段和相邻调用的buffer复用均保留。flush还覆盖此前已发起但尚未完成的信号尾部；其目标可见保证仍需匹配provider实现和校准。

Dispatch的`slots`是运行时原子分配的符号变量，同一call/source/destination组内应映射成0到group_size-1的双射。计划没有把它们提前赋值。Combine的`binding.kind=from_dispatch_slot`保存来源rank前缀及槽公式，取得实际slot后才知道通道/连接；各通道内的循环顺序也须届时解析。`deferred_constraints`列明这些未执行约束。数组排列是保存格式，不代表不可比较操作的GPU发起顺序。

`layout`给出该分支token put和共享EP buffer大小；不含工作区、队列、KV、权重或完整显存检查。H7168/Top8时Dispatch/Combine put为14,464/14,400B，不能只算hidden。源码和范围见[布局核查](../../docs/reviews/现代通信布局与并发槽位_20261022.md)。

## 检查必要依赖

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout modern-deepep-check \
  --plan runs/my-modern-plan --output runs/my-modern-zero-check.json \
  --withhold m0.dispatch.expert_count.s2.d1.visible
```

示例rank2无token，但其零计数块仍是必要依赖。`--withhold`可重复，必须使用确实存在的完整事件ID；它仅计算假设暂扣事件后的可达闭包，不是丢包、超时或时延实验。`passed=true`表示表示完整且检查执行成功；`all_completions_reachable=false`表示有被阻塞的应用阶段。未知事件、篡改、超预算及不支持的模式报错，不写正常结果。

新目录独立流程与单元反例见[验收](../../fixtures/modern_deepep_plan/README.md)。动态约束尚未执行，包级后端、持久QP、原子去重、CQ/源buffer和实机校准均未由本计划完成。
