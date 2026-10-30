# 完整片段副本与逻辑结果

`whole_call_replication`提供显式eager完整片段冗余基线：同一批输入在两个独立完整专家并行（EP）组执行同一个有限MoE片段，控制层只接受第一个整图完成的结果。两组保留全部Dispatch/Combine门限、计算、实际消息和ACK，较晚尝试继续真实运行和清理。没有将单贡献赢家替换为原图输入；这是成熟基线，不是新恢复算法。

## 输入与初态

已有LLMServingSim `moe-scaleout-service/1`服务可增加：

```json
"whole_call_replication": {
  "schema_version": "moe-whole-call-replication/1",
  "policy": "eager_complete_segment/1",
  "initial_state": "declared_equivalent_ready_kv/1",
  "max_events": 7,
  "calls": [{"id": "segment0", "primary_group": 0, "replica_group": 1}]
}
```

call代表一批请求的完整声明片段，两个group是`parallel.groups`索引。所有组须恰被call两两覆盖、大小相同、物理rank不重叠。最多32个call，各需7条生命周期事件；预算不自动扩大。要求一个Decode步、显式稀疏来源、`ep_transport_flush/1`、`rank_reduced/1`、持久传输和执行事件`/1`，不与单贡献`inflight_helpers`混用。

`route_replay.requests`只声明primary组的唯一原始请求，每组至少一份。按组内相同位置和slot显式派生replica，保留原file_id、文件SHA、原路径、Decode入口、层、专家选择和上下文，派生记录增加`replica_of`。不能另外向replica组填源请求，也不放宽原始文件唯一性或来源校验。默认关闭时仍禁止循环/克隆独立流量。

例如4份独立原件对应4个逻辑请求、8次物理执行，不是8份独立真实数据。源文件只快照一次；真实LLMServingSim为每个物理尝试分别建立请求、保留KV并在各自实际图完成后释放。每GPU只有本组活动图，两个组是不同物理计算资源，并非让一张卡有两倍算力；全部尝试共用同一ns-3时钟、NIC和Fabric。

两组必须已有完整专家副本和声明等价ready-KV。原始路由没有权重、激活、KV内容或采样状态，不能验证数值等价。若实际部署需要建立副本、迁移KV或跨GPU继续计算，必须另计传输、计算和存储成本。`capacity.json`只核算本模式声明的有限片段与KV，不证明完整671B部署容量或GPU时延。

## 执行与输出

两图在初始COMMIT立即提交。`WholeCallConsumer`只在已验证的`graph_retired`接受完整结果；节点或消息提前完成不会触发提交。每call只发一个`client_result_ready`，同刻按实际回调顺序选择。它面对已声明片段的控制层消费者，不包含真实输出张量传到客户端的开销，不在原GPU上提前启动未模拟的后续层或新请求。

| 输出 | 含义 |
|---|---|
| `whole_call_events.jsonl` | `moe-whole-call-event/1`，提交、完整结果、一次逻辑就绪及退出；连续sequence、绝对纳秒，结果另含来源`execution_sequence`。 |
| `whole_call_report.json` | `moe-whole-call-report/1`，两个尝试、赢家、逻辑/物理请求数和退出状态。 |
| `frontend.whole_call_replication` | 同一状态；原`frontend.requests`仍列物理尝试，不提前伪造原调度请求完成。 |
| `application_finished_ns` | 本模式所有逻辑call各有一个完整赢家的最晚时刻，仍是有限片段，不是完整LLM/EOS。 |
| `attempts_finished_ns` | 本模式新增，最后一个实际尝试退出；原终态COMMIT仍在此时刻。 |
| `network_drained_ns` | 所有尝试与网络真实清理完成；`finish_ns`保留旧停止/排空含义。 |
| `post_application_drain_ns` | 排空减逻辑就绪，包含背景计算，不能全部写成网络传输等待。 |

本模式时间另有`timing_scope=eager_whole_finite_segment/1`；默认口径不变。直接调用`service_terminal`须给`config=实际输入`，不能凭前端额外字段改变完成解释。公共run/check/sweep已传入输入。

`input_identity.kind=declared_route_and_ready_kv_lineage/1`的SHA仅对模型、成本、传输表示、原路由身份、位置及初态声明取摘要，不是张量或KV内容摘要。异常清理保存已验证状态，宿主直接杀进程可能缺文件；失败不补完成。当前底层服务失败仍按失败处理，即使已有赢家，也不将背景清理失败排入成功性能排名。

## 核验与限制

`rdma-check`检查实际节点、阶段门限、完整专家工作、真实接收/ACK，再从唯一源文件重建显式副本与整个结果消费者，核对全部事件、报告和赢家。原尝试必须真实退出，不伪造ACK、提前释放原KV或删除图。相对来源快照支持目录迁移。

某副本早于同次执行的原尝试，不等于相对无冗余控制的净收益；仍须同资源原地SR、成熟延迟备用、正常开销和误判/失败对照。真实路由不使声明GPU成本成为实测。目前没有取消已启动尝试、后续请求抢占或数值验证。

## 延迟启动完整副本

除上述立即副本外，还支持延迟启动。把配置的`schema_version`改为`moe-whole-call-replication/2`、`policy`改为`delayed_complete_segment/1`，再增加`hedge_after_ns`（1～4,999,999ns的绝对启动门槛）和`max_wakeup_events`（2～65,536）。其余范围与立即副本相同，不与watchdog或贡献观察器混用。准备脚本的`--hedge-after-ns 70000`会另生成`service-delayed.json`，不改变立即副本及无副本输入。

primary在0提交，replica先持有实际预留KV，但不进入LLMServingSim的运行/等待队列。一个可取消、可替换的原生ns-3定时事件唤醒控制层，所有图与网络共用这一个时钟。截止决策时若已有实际完整primary结果，则释放未启动副本KV并记录`attempt_skipped`；否则在门槛时刻通过实际`add_decode`和`schedule`启动副本。不能读取未来丢包或队列。同刻事件以该次原生DECIDE之前实际收到的完成前缀为准；已启动者继续完成。

新后端需支持`--control-wakeup MAX_EVENTS`，公开run从配置自动添加。该原生选项要求持久传输和执行观察，不与helper/watchdog控制混用；旧后端明确拒绝。默认模式不创建定时事件或额外报告。预算包含arm/cancel/fire，替换前预留后续触发事件的记录空间，超预算在改变定时状态前拒绝。

输出`moe-whole-call-report/2`区分`reserved_attempt_requests`与实际提交的`execution_attempt_requests`。未启动请求为`NOT_STARTED`，保留原始`scheduler_state=WAITING`、原始token计数和空ITL；不生成执行结束时间。`frontend.standby_memory`记录初始副本KV、实际跳过/完成时的释放及最终占用。模型与等价ready-KV仍为预先存在的声明初态，未模拟建立成本。`all_attempts_retired`表示所有尝试已完成或未启动即释放，不表示每份都执行过。

`result.service.control_wakeup`记录有序arm/cancel/fire及待触发时间，原始DECIDE含`wakeup_ns`，COMMIT含`wake_at_ns`。公共核验按原始OBSERVE/DECIDE顺序重建策略，核对整图覆盖、跳过状态、原生定时事件和按原始上下文计算的KV释放。时间口径为`delayed_whole_finite_segment/1`；仍分列逻辑就绪、已启动尝试结束和网络排空。

### 为独立任务设置不同门槛

显式`moe-whole-call-replication/3`沿用`delayed_complete_segment/1`，移除顶层`hedge_after_ns`，改为每个`calls`条目必填`hedge_after_ns`，范围仍为1～4,999,999ns且必须早于本次模拟截止。`max_wakeup_events`仍为全服务预算，不自动扩大。旧`/1`立即副本及`/2`统一门槛保持原格式和语义。

每个primary仍在0提交。控制层只为尚未完成、未启动/未跳过备用的call选择最早期限，共用一个可替换的原生定时器。某call提前完整完成，只释放自己的备用KV；更晚的任务继续等待自己的门槛。到期只启动该时刻到期且尚无完整结果的call，不能借用另一call的门槛。所有已启动图继续真实收尾，实际结果与资源分别核验。

这可以比较一个任务启动备用时对其他独立任务的影响。给其他任务较晚门槛仍表示预先持有完整模型/KV，不能冒称未预留资源；它们实际未启动副本须由结果核对。公开[双任务128端点Clos示例](../examples/scaleout_multicall/README.md)采用四组EP32，支持两份独立人工原件或显式提供的R1来源清单。

准备与公开命令见[128端点Clos示例](../examples/scaleout_whole_call/README.md)。

### 多步Decode的完整备用

显式`moe-whole-call-replication/4`沿用逐call门槛格式，允许`workload.decode_steps`为1～128。旧`/1`、`/2`、`/3`仍只接受一步，原报告语义不变。公开双任务准备脚本的`--decode-steps 2`生成`/4`；脚本提供1～4步并在执行前规划所有步骤和全部可能备用的连接/消息预算，原件不足时拒绝，不能循环补齐真实路由。

一个物理尝试顺序执行`gN.w0`至最后一步。前一步真实整图退休后，LLMServingSim立即安排同组下一步；同组不能并发两步。一次Decode步仍含全部声明层、Dispatch/Combine门限、计算、接收和源确认。中间图完成不能作为call结果，也不能跳过备用或释放已启动备用KV；最后一步退休才提交完整有限片段。已启动的落后尝试即使另一个已获胜，仍继续余下步骤和清理。

`moe-whole-call-report/3`增加`decode_steps`，每个attempt的`waves`记录实际逐步图、提交与退休时间；`graph_id`保留首图身份，`submitted_ns/finished_ns`覆盖整个尝试。输入身份摘要包含片段步数，不能将两步和三步标为同一任务。实际源路由、原生决策前缀、每个后继图与真实前驱完成、全部图覆盖、最终结果和各call实际KV释放均被公开离线核验。

门槛仍为从0开始的绝对仿真时刻，比较的是完整固定片段的备用；不会每步重新计时。只存在首步结果时，门槛仍保留。整个片段完成早于门槛才跳过未启动备用。此能力是有限连续Decode，不含新请求到达、持续服务吞吐、KV副本建立或数值等价验证，不是新的恢复算法。

已保存的真实路由正常/局部故障功能验收见[结果与证据](../fixtures/whole_call/README.md)。两条件均开启立即副本，不是无冗余净收益对照。

### 源确认超时才启动完整备用

显式`moe-whole-call-replication/5`保留多步及逐call绝对兜底`hedge_after_ns`，每call增加必填`source_ack_timeout_ns`（null为关闭，启用为1～4,999,999ns），顶层增加`max_guard_messages`和`max_guard_events`（各1～65,536）。至少一个call启用；仍要求显式完整副本、持久传输、事件/1与原阶段完成依赖，不与`source_ack_watchdog`或helper入口混用。

只跟踪启用call的primary实际已提交图。初始图/后继图在实际提交时登记消息身份；实际SEND post记录源端期限，实际源完成回调取消该消息候选。原生定时DECIDE检查当时尚未确认且已超过期限的消息；若存在才启动一次完整备用，或者在整段绝对兜底到达时启动。可以在其他真实DECIDE时刻发现同样的过期状态，不回填过去的提交时间。不读取接收端真值、未来故障或交换机路径。

首个检查为从0起的期限；随后为整段兜底、当前时刻加期限、当前未确认消息post加期限三者中的最早将来时间。即使没有未确认消息，也保留周期检查，以覆盖之后才提交的通信。同一服务仍只有一个可替换原生控制定时器。此为轮询已有源确认超时谓词，可能比某条消息自己的期限晚；不是逐消息精确定时watchdog，也不制造`SOURCE_ACK_TIMEOUT`帧。全段结果提前完成才跳过备用并真实释放KV；阶段确认通过不代表永久禁用保护。

`max_guard_messages`限制实际已登记primary消息总数，跨步骤累计；`max_guard_events`限制记录的逐call检查行，不是包数或硬件容量。预算超限明确失败，不自动扩容。已有`max_wakeup_events`继续限制arm/cancel/fire，消息、图、执行事件和磁盘仍由原预算约束。

新报告`moe-whole-call-report/4`保持整个多步尝试与逻辑结果，并增加`source_ack_guard`：实际post/确认状态和每次检查的来源sequence、pending/overdue消息、原因及下次检查。源超时、兜底、已启动和完整结果分列。`rdma-check`按原始OBSERVE/DECIDE交错顺序重建检查和授权，再核对实际图、原生定时、全部步骤、结果与KV。已启动输家仍执行、确认、清理；没有取消或数值等价验证。此为成熟延迟备用的输入和执行能力，不代表新恢复算法或收益。

平台CPU、公开输入准备与旧网络兼容性证据见[验收说明](../fixtures/ack_guard_whole/README.md)；真实路由研究的收益与代价独立评价。
