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

已保存的真实路由正常/局部故障功能验收见[结果与证据](../fixtures/whole_call/README.md)。两条件均开启立即副本，不是无冗余净收益对照。
