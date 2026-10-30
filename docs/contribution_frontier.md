# 专家贡献就绪与消费窗口诊断

`scaleout contribution-frontier`读取一次已经完成、启用贡献跟踪及helper的LLMServingSim Scale-Out服务，检查“某个备用提前返回”是否同时提前了“这个请求所需全部专家结果到齐”。它不启动网络、不提交任务、不修改原记录，也不预测改变策略后重新运行的时延。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout contribution-frontier \
  --run /path/to/completed-run --output /path/to/new-diagnosis
```

输入需含`contribution_tracking`、`inflight_helpers`、实际`shared_compute`、阶段门限及完整执行记录。命令先执行与`rdma-check`相同的核查，从来源/图重建贡献与实际回调，再读取已核验的`contribution_calls.jsonl`、`result.json`和图。只接受完整成功执行；不支持SimAI服务或缺少helper/shared-compute证据的旧格式。失败执行的通用恢复诊断仍用`rdma-diagnose`。

输出目录必须尚不存在，且不能在输入运行目录内部。结果包含`summary.json`、逐调用`calls.json`及逐贡献包`bundles.csv`。执行前后对输入全部文件作SHA256核对，`input_files_unchanged`为原件数量；它不是重跑复现次数。结果中的`native_network_executed_by_diagnosis=false`仅表示诊断没有启动新网络，输入记录来自实际原生仿真。

每个调用是一个请求在一个MoE层和一个Decode步的专家贡献集合。按rank归并的包可以含多个专家；检查覆盖每个预期专家，保留全部并列最后结果，不能只选一条最慢边。

- `original_all_ready_ns`：本次记录中所有原件结果回调时刻的最大值。
- `winner_all_ready_ns`：每个贡献取实际最先返回的合法尝试，再对所有预期贡献取最大值；独立核对已记录赢家和完整集合就绪时刻。
- `recorded_frontier_advance_ns`：上述两值之差，仅是固定记录内的就绪前沿差。去掉helper会改变竞争和调度，因此不能称为无helper对照的实测加速。
- `individual_advance_ns`：单份贡献的原件/赢家时间差。它与完整集合的提前量分列。
- `merge_ready_ns/start_ns/done_ns`：原图对应rank聚合merge的实际共享GPU FIFO记录。同rank多个请求可能共用同一merge节点，不能把重复记录相加。
- `gate_strictly_last_dependency`：专家节点的Dispatch门限晚于其全部其他直接前驱，并核对实际issue和开始。这证明记录中的门限是最后前驱，不证明删除门限合法或相应等待可全部消除。
- `expert_fifo_wait_ns`：实际GPU FIFO开始减入队，和等待输入/门限分开。当前计算模型为声明耗时，不是实机SM竞争。

时钟均为本次仿真的纳秒。汇总只统计调用数和各项最大值，不累加不同调用时延。`helper_actual_logical_bytes`为本次实际提交的备用输入和结果字节，包含其真实消息，不含重传重复字节；不能与不同输入/部署的历史完整备用直接比较。

若输入开启了`message_buffers`，还检查赢家目标缓冲在全贡献汇合时是否仍保留。`winner_buffer_live_at_join`为true表示汇合早于释放，为false表示汇合晚于释放；同纳秒只凭时间无法证明回调顺序，输出null。没有捕获或本地结果没有物理消息时，也不虚构缓冲证据。`minimum_extra_hold_ns`是从既有释放时刻到这次记录的汇合时刻的正差，未包含实际消费者合并的排队/计算/跨层占用。

现有功能模式没有消费者，helper退休时释放目标范围是其合法行为。诊断出的早释放是未来消费者必须新增保留引用或复制结果的要求，不是当前实现的越界读或上游通信库漏洞。字节仍是标记，不证明原件与备用数值等价。

若在结果返回时立即复制或归并进独立累加器，也可避免保留原目标范围到全集合汇合；必须另外计入累加器容量、计算、回收和数值顺序。`minimum_extra_hold_ns`只适用于未来在该汇合时刻仍读取原范围的消费者，不是所有消费设计都必须增加的保留时间。

新机器完整准备、运行后再诊断见[使用示例](../examples/contribution_frontier/README.md)。本轮真实路由及人工记录的解释另见[研究结果](../experiments/consumption_value/README.md)。
