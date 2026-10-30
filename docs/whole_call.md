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

目前立即启动冗余，尚无延迟触发、取消、后续请求抢占或数值验证。某副本早于同次执行的原尝试，不等于相对无冗余控制的净收益；仍须同资源原地SR、成熟延迟备用、正常开销和误判/失败对照。真实路由不使声明GPU成本成为实测。

准备与公开命令见[128端点Clos示例](../examples/scaleout_whole_call/README.md)。

已保存的真实路由正常/局部故障功能验收见[结果与证据](../fixtures/whole_call/README.md)。两条件均开启立即副本，不是无冗余净收益对照。
