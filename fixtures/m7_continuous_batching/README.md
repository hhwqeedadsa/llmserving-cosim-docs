# M7 同 rank 多请求连续批处理

本样例使用一个 EP 组、两个逻辑 rank（4、10）映射到 UB 节点（1、3），执行 KV 已就绪的纯 Decode 和一个选定 MoE 层。模型、路由和计算系数均为合成参考值。完整模型容量沿用 [M6 的模型公式](../m6_service_model/README.md)，没有下载模型、测量硬件或修改依赖。

## 输入与调度

服务使用 `moe-ub-configured-service/2`，层使用 `moe-ub-layer-fixture/3`。服务的 `scheduling` 必须等于层的 `batch_limits`，分别声明每 rank 的 `max_num_seqs` 与 `max_num_batched_tokens`。前者限制驻留请求数，后者限制每轮实际 Token 数；每个选中请求每轮恰好一个 Token。因此驻留数可以超过本轮选中数。两个预算均为 1～4096，功能图的总 Token 数另有 4096 上限，不表示验证了这个规模。

层的 `compute_dtypes` 声明 compute、kv、dispatch、combine，必须与服务模型一致；权重精度继续由 model_spec 负责。可选 `deployment` 继承 M6 的 die/chip/server 分类，没有填写时沿用显式 UB 映射，不推测本地性。旧 service/1 与 layer/1、layer/2 保留单请求语义，不能把新层静默交给旧服务。

组正在执行时，新到达请求只进入外部 ready-KV 队列；只有该组真实完成后的空闲边界才接纳。接纳按到达顺序，同刻按 request_id 排序，检查序列槽和最终输入 KV 所需块数；队首暂时放不下时等待，不越过队首，不抢占、重算或 swap。单请求最终需求超过该 rank 总 KV 容量时，在启动后端前拒绝。

接纳时在原生 Scheduler 的唯一块池中预留 `ceil((output−1)/block_size)` 块，预留不推进 Token。原生 `schedule()` 按 Token 预算选择驻留请求，`add_done()` 在真实轮次完成时逐请求记账、结束和释放。尚未选中的驻留请求仍持有全部预留块；外部等待请求没有目标 rank 驻留声明。服务结束要求每个请求完成且释放一次、KV 占用为零。

通信缓冲下界为 `max(B×R×min(TopK,R−1)×H×(Dispatch元素字节数+Combine元素字节数), 2×send_slots×chunk_bytes)`，其中 `B=min(max_num_seqs,max_num_batched_tokens)`。这是保守的最大批次向量预留，不根据某个样例的低流量放松预算。

## 可独立核算的 A/B/C

三个请求均到达 rank 4。专家 0、1 在 rank 4，专家 2 在 rank 10，Top-K=1；每个向量为 32×2=64 字节。

| 请求 | ID | 到达 ns | 初始 prompt / generated | 剩余 Token | 逐步专家 |
|---|---:|---:|---:|---:|---|
| A | 201 | 0 | 2 / 1 | 3 | 2、0、2 |
| B | 202 | 0 | 5 / 1 | 1 | 2 |
| C | 203 | 1000 | 7 / 2 | 2 | 2、1 |

序列和 Token 预算均为 2。rank 4 有三个 512 字节 KV 块，每块八 Token；A/B/C 最终输入分别为 5/6/10 Token，占 1/1/2 块。

| 轮次 | 请求 / Token 索引 | 启动→完成 ns | fabric 业务字节 | UB 写 |
|---|---|---:|---:|---:|
| 0 | A/2、B/5 | 0→21100 | 256 | 2 |
| 1 | A/3、C/8 | 21100→38196 | 128 | 2 |
| 2 | A/4、C/9 | 38196→55292 | 128 | 2 |

C 在第一轮中到达；B 于 21100 ns 完成并释放一块，C 此时接纳两块。每轮生成量为 2，A/B/C 最终 reached 分别为 6/7/11。不同请求即使选同一专家也各保留一份向量；只在同 Token 的相同目标 rank 内去重。chunk=256 时第一轮的两个向量合成一次 128 字节 Dispatch 和一次 128 字节 Combine，共六次写、512 字节。

把 rank 4 容量减到两块，批次变为 AB、A、A、C、C；A 继续运行，释放后才接纳 C。序列预算 1、Token 预算 2 时依次 A、A、A、B、C、C。序列预算 3、Token 预算 1，容量增加到四块且三个请求同时到达时，首轮仍只选 A，但 A/B/C 均驻留并占 1/1/2 块。

## 计算与执行记录

输出 `moe-ub-execution-plan/3` 在旧计划字段之外保存 batch_limits 和 compute_inputs。后者列出各 rank 的实际请求、Token 索引、上下文长度（索引+1）、query_tokens=1、批次规模、各专家的输入数（含零）与精度。Python 和 C++ 各自核对这些字段及实际节点耗时；运行中不能改变 batch_limits。

本轮计算仍使用显式线性参考系数：准备/结束工作按本地 Token 数缩放，专家工作按本 rank 的专家输入总数缩放。记录标明 `context_sensitive=false`、`expert_distribution_sensitive=false`；上下文和逐专家输入可追溯，但当前系数没有复现长上下文或专家负载分布对内核时间的影响。该依据仍为 `unvalidated_reference`，相应模型与真实数据工作属于 M9。

`service_result.json` 增加 batch_events，记录逐请求 scheduled_tokens、驻留成员、上下文、每批生成量及完成请求；memory_events 记录接纳、等待原因、释放与同一块池快照。每 rank 仍只有一张活动图和一个计算槽；Dispatch/Combine 仍使用真实 UB 完成事件。多层、多请求、多组组合属于 M8，本任务未执行。

## 已验证入口与对照

独立构建配置见[环境记录](../../docs/environment.md#2026-10-03m7-同-rank-多请求连续批处理)。在根目录使用已有虚拟环境，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_continuous_batching.py \
  --build-dir build/m7-continuous-batching --output-dir runs/my-batching-check
```

检查包含 15 次正常执行及 11 项预期拒绝。除上述容量和预算对照外，还核对空服务、迟到后重新启动、不均匀 rank 批次、批次缩放、32 字节分块、纯同 rank、混合同芯片与跨芯片路径，以及来源开/关/不挂观测。分块对照共 16 次写且仍为 512 字节；混合本地性的一轮共 4 次写、1152 字节和 4 次同芯片交付。

网络改为 1 Gbit/s、2000 ns 后，实际数据就绪和后续轮次推迟，总完成时间从 55292 变为 105978 ns。来源开/关/不挂观测执行结果一致，端口整包与内部业务字节分别守恒。

M6 的 scenario YAML 入口也可引用这组新 schema；解析记录明确标记连续批处理范围。原 M6 样例仍使用旧 schema。测试还独立改动计划中的请求身份、计算形状、预算、来源和计算节点，确认后端在 0 ns、尚未提交业务写时拒绝。
