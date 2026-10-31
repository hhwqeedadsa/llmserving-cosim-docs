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

## 可选压缩存储

完整计划超出明文16MiB时，可在上述`modern-deepep-plan`加入`--format compressed-json`并指定新输出目录。生成`plan.json.zlib`和manifest/3；`modern-deepep-check`自动识别，不需要额外参数。标准Python的`zlib.decompress`可读取内容；输入文件原样保留。解压后与相同配置的flat规范计划逐字节相同，含全部零计数、屏障、self-peer和未解析slot。

默认`flat`仍为原schema/2及`plan.json`。两种格式的逻辑操作/事件上限与总保存产物16MiB上限相同；新格式展开JSON另外限64MiB，检查拒绝截断、额外流/尾部、越界、错误声明和重算hash后的语义篡改。保存空间减少不等于运行期内存、原生执行规模或网络性能提升；现代native仍使用输入配置重新编译，未获得新的运行期预算。新目录公开验收见[压缩格式证据](../../fixtures/modern_plan_storage/README.md)。

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

默认flat输出为`moe-modern-deepep-partial-order/2`、清单为`moe-modern-deepep-manifest/2`，输入格式不变。`path`分为`local`（显式本地指针/存储旁路）、`gin_self`（发给自己但经过GIN及self-peer QP）和`rdma`（经过GIN发给其他rank）。固定源码中的屏障signal和专家计数块即使src=dst也走GIN；rank计数的put_value及token本地复制才旁路。self-peer的物理回环路径和服务成本尚未建模，不能直接略去，也不能按普通跨rank链路发包。expert计数块另保留`source`的发送区region、epoch和元素偏移，目标区与源区分开。

旧版`/1`将所有同rank操作归为local，遗漏了provider资源分类。`modern-deepep-check`明确拒绝旧清单，请用保存的`input.json`重新执行plan；不自动修改历史结果或把旧性能结论迁移到新版本。更正依据及新流程见[自发GIN语义验收](../../fixtures/modern_self_gin/README.md)。

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

新目录独立流程与单元反例见[计划验收](../../fixtures/modern_deepep_plan/README.md)。计划本身不执行动态约束；独立事件核心的用法如下。包级后端、持久QP、原子去重、CQ/源buffer和实机校准均未由本计划完成。

## 逐事件执行

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout modern-deepep-replay \
  --config examples/modern_deepep_partial_order/input.json \
  --actions examples/modern_deepep_partial_order/actions-prefix.json \
  --output runs/my-modern-prefix.json
```

`actions`只含`schema_version="moe-modern-deepep-actions/1"`及`events`字符串数组，按声明发生的顺序列出计划事件ID。示例只触发rank0的kernel begin，不假造后续完成；空数组可查询最初就绪事件。可提供完整序列或任意合法前缀。结果中的`ready_events`列出下一步允许触发的事件，调用者仍需根据真实调度或完成回调选择。CLI核验这份声明是否满足依赖，不能证明这些事件曾在真实设备发生。

Python公开功能接口位于`moe_ub_cosim.modern_deepep_runtime`：

```python
from moe_ub_cosim.modern_deepep_runtime import ModernDeepEPRuntime
runtime = ModernDeepEPRuntime(config)  # 与计划入口相同的配置对象
runtime.ready()                      # 查询，不推进
runtime.fire("m0.dispatch.r0.begin")  # 仅在调用者实际开始该阶段时触发
state = runtime.snapshot()
```

`fire(id)`仅记录一个已就绪事件，不递归发起传输。重复、未知、前驱未完成的事件会拒绝。slot分配触发时才增加对应call/source/destination计数器，不能自行传入slot值；同token的全部目标slot分配结束后才允许put。Combine按实际slot与source前缀取得接收序号，解析SM、warp、context、connection及声明设备。同一owner/SM/rotated-warp的下一次归约必须等前一接收序号的put发起，不等待其完成；不同通道保持并发。

`operation(id)`只允许读取已就绪或已经发起的操作，返回绑定和目标偏移的副本，供未来适配器在提交前检查。该调用不reserve队列、不提交WQE，也不释放buffer。`accepted/visible/local_complete`必须分别由未来provider实现报告；目前没有该实现，不能在研究程序里遍历ready并自动制造完成，再称作网络仿真。`all_application_complete`不表示所有信号尾部都完成，`all_events_fired`也不构成物理网络排空证明。

命令沿用相同模型与事件数上限，actions文件≤16MiB且事件数≤配置max_events，输入+actions+结果≤max_output_bytes≤16MiB；卷预留8GiB，输出文件及父目录规则与check相同。人工功能用例、独立事件顺序核验和拒绝条件见[事件核心验收](../../fixtures/modern_deepep_runtime/README.md)。

## 窗口、计数与源缓冲检查

用相同配置和事件序列检查相对窗口地址、发送源的使用期限、计数消费清零和滚动信号：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout modern-deepep-window-replay \
  --config examples/modern_deepep_partial_order/input.json \
  --actions examples/modern_deepep_partial_order/actions-prefix.json \
  --output runs/my-modern-window-prefix.json
```

示例仍只触发一个begin事件；不会自动制造网络完成。Python接口为`moe_ub_cosim.modern_deepep_windows.ModernDeepEPWindowRuntime(config)`，提供相同的`ready/operation/fire/snapshot`方法。`operation`增加`memory`中的源、目标映射；只有实际分配slot后才返回Dispatch目标地址。命令与replay采用相同输入、总产物及预留空间限制。

| 状态 | 表达方式 |
|---|---|
| 应用主窗口 | workspace从0开始，EP buffer从16MiB开始；偏移相对抽象注册区，不是GPU虚拟地址或rkey |
| 计数 | send/recv为同一workspace中不同区域；rank与expert子区连续，收到编码负值才算已发布，消费后重置为0 |
| GIN索引信号 | 独立provider域，按owner/context/slot定位；64位值及本地shadow连续累加，不映射进应用workspace |
| 跨轮复用 | epoch用于说明版本，不能把同一物理偏移当成不同内存；Dispatch和Combine共享EP区 |
| 源读保护 | bulk put与远端token issue后取得只读lease（使用记录），收到local_complete后释放；visible不会释放，重叠写会拒绝 |
| 本地token | Dispatch从TMA共享内存复制，Combine本地归约写目标；不伪造GIN源读保护 |

固定源码的`EPSignals`含较大的静态数组，按C++数据布局为15,831,168B，按2MiB分配粒度取整后为16MiB。这是分支布局，不是已测显存占用或单次网络流量。`layout.shared_ep_buffer_allocation_bytes`此前只表示EP数据区；完整主窗口还包括workspace，亦不包含权重、KV、NCCL队列及其他内存。

输出`moe-modern-deepep-window-execution/1`保留事件核心字段，增加`windows`、逐issue的`memory_log`及`memory_state`。计数和信号是根据调用者宣告的visible回调与操作数更新的影子状态；没有接收真实传输负载，也没有核对token数值或GPU内存序。源写版本在首个相关issue处建立检查边界，不表示测得TMA或notify reduction时刻。`tracked_source_lifetime_validated=true`只覆盖这些显式相对范围；完整`buffer_lifetime_validated`仍为false。self-peer也保留请求及源读义务，但未赋予回环路径或服务代价。

源码身份、原始结构体的宿主ABI检查、新目录公开流程及失败记录见[窗口验收](../../fixtures/modern_windows/README.md)。该接口不能用来给现代DeepEP排性能名次；后续仍需将完成回调、实际负载和provider服务接入同一网络时钟。
