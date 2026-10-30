# 贡献包与执行尝试

`ContributionAttempts`记录一个token在一个原始MoE层中需要的专家贡献、原/备用尝试和资源退出。它是调用方回调驱动的无计时核心，没有定时器、网络/GPU调度或张量计算。独立公共命令见[自包含示例](../examples/contribution_attempts/README.md)；可选[实际服务绑定](contribution_binding.md)使用同一次ASTRA/ns-3事件驱动原尝试，默认服务不改变，尚不调度备用。实现见[contribution_attempts.py](../python/moe_ub_cosim/contribution_attempts.py)，研究依据见[已有工作审查](reviews/专家副本恢复的已有工作与实施边界_20261030.md)。

## 输入身份

规格`moe-contribution-attempts/1`恰有`schema_version,invocation,expected_experts,bundles,replicas,limits`。

`moe-contribution-attempts/2`沿用覆盖和生命周期行为，将`token_position/input_sha256`替换为有类型的`token_coordinate/input_identity`，明确表示原路由入口和仿真来源，不伪造激活摘要。其完整格式及声明执行/路由摘要的边界见[绑定说明](contribution_binding.md)。下表继续定义旧/1规格，原有输入与输出保持兼容。

| 字段 | 定义与约束 |
|---|---|
| `invocation.request_id` | 请求身份，1–128位ASCII字母/数字或`_.:-`，不能只用可复用batch槽位。 |
| `decode_step/token_position` | 0–`2^31-1`整数；`source_layer`为0–127原始层号。布尔值不是整数。 |
| `execution_sha256` | 声明的模型、权重版本、精度和执行约定共同指纹，64位小写十六进制。 |
| `input_sha256/routing_sha256` | 声明的激活输入和路由/权重指纹；原路由文件hash不能冒充真实激活hash。 |
| `expected_experts` | 应有专家，1–8个、升序唯一、0–255。 |
| `bundles` | 1–8项`{id,experts}`，互不重叠且恰好覆盖expected_experts。id遵循请求身份的字符规则。 |
| `replicas` | 1–512项`{rank,execution_sha256,experts}`；rank唯一且0–511，专家集合最多256个。开始尝试时验证版本匹配及完整专家覆盖。 |
| `limits` | `max_attempts_per_bundle`为1–8，`max_resources`为1–4096，`max_events`为1–8192；均为整个实例生命周期预算。 |

一个实例负责一个请求的一步、一个原始层及一个token。当前rank内合并Combine下，恢复单位是同一token在目标rank上的完整专家集合，称为贡献包，不能任意拆开合并结果后按字节推断专家完成。

`bundle_key(id)`是`{"invocation":完整对象,"experts":此包升序专家列表}`的规范JSON的SHA-256，编码为`sort_keys=True,separators=(',',':'),ensure_ascii=True`。显示用bundle id及执行rank不进入逻辑身份，原/备用可提供相同贡献；请求、步、层、输入、路由或专家变化会改变身份。

摘要只验证声明身份一致，不认证网络发送者，不验证张量或数值等价。调用方须证明真实权重、精度及执行约定一致；本模块不加载权重。

## 事件与状态

`apply(event)`是唯一变更接口。每项都有`kind,time_ns,attempt`。时间为非递减的0–`2^63-1`整数，来自调用方共同仿真时钟；本模块不自行走时。同刻结果按回调调用顺序决定先到者，不按rank或名称赋予优先级。

| kind | 额外字段 | 约束与效果 |
|---|---|---|
| `start` | `bundle,rank` | 验证副本、次数和唯一attempt；已提交包不能再开新尝试。 |
| `resource_open` | `resource` | 登记provider实际取得的租约；失败或结果返回后不能获取新资源。 |
| `resource_close` | `resource` | 所属attempt的实际关闭回调，只能关闭一次。 |
| `input_ready` | 无 | 输入就绪，可记录失败后的迟到输入，不自动启动计算。 |
| `compute_begin` | 无 | 输入就绪、未开始且未失败后，记录实际开始。 |
| `compute_done` | 无 | 已启动计算完成；取消后仍须保留已发工作和成本。 |
| `result` | `key_sha256` | 完整结果回调，检查身份及计算完成；首个有效结果提交。 |
| `fail` | `reason` | 拒绝后续提交，reason为`provider_error/cancelled/deadline`；不自动关连接、停包或启动备用。 |
| `retire` | 无 | 须已有结果或已失败、启动的计算全部完成、登记资源全部关闭。 |

result输出为`committed/redundant/duplicate/late_failed/late_retired`。迟到/重复也核对身份；不能因旧attempt退休就接受错误版本。备用获胜不自动取消原尝试，后者仍可能继续计算并返回冗余结果。非法事件在改变状态、时钟、ID记录和预算前拒绝。

ID使用记录保留到实例结束。resource是租约身份，不是物理显存地址，复用须用新代际；本实例尚不能检测不同租约是否别名同一缓冲，也不能发现跨实例未退出资源，后续provider绑定须补齐。

`snapshot()`返回深拷贝，保存keys、winners、accepted/missing专家、attempt阶段、资源和有序事件。`logical_complete_ns`表示最后应有贡献提交；缺贡献为空。`all_started_attempts_retired`只表示已登记尝试全部退休（零尝试时为真）。`declared_call_retirable`要求前两项同时成立，只在所有provider租约均完整登记时有意义，不能替代网络排空或缓冲保护。

**结果获胜不生成原RDMA ACK，不关闭原传输、不释放源缓冲。** 两个尝试都失败时，资源清理完也不会伪造逻辑完成。已启动计算在取消后尚未完成，尝试不能退休。

## 命令与输出

`scaleout contribution-check --config INPUT.json --output NEW_DIR`接受≤1MiB的`moe-contribution-transcript/1`对象，恰含`schema_version,spec,events`，无外部引用。JSON重复键和超事件预算拒绝。全部事件合法后才创建目录；已有目录拒绝覆盖。

`input.json`原样复制输入。`report.json`保存`moe-contribution-state/1`、`transcript_sha256`、`transcript_valid=true`及状态。退出0只表示事件合法；未到齐或未退休的合法前缀也返回0，必须分别查看状态。非法输入返回1，不创建结果目录。

`execution_scope=untimed_caller_callbacks_no_ASTRA_ns3_or_tensor_validation`明确来源。人工回调时间不能用于计算加速比。

## 接入服务的剩余工作

原尝试可经可选绑定接入同一次实际服务；仍需支持备用执行及其真实资源代际，计入检测、传输、GPU排队/计算、返回和清理成本。原合法门限和回调须保留或显式重构，不能删断言、伪造ACK。本模块不选择副本或恢复时机，不代表任意前端任务已支持副本恢复。后续网络仍选择128/256/512中的一档Clos。
