# 检查DeepEP低延迟操作的依赖

`scaleout deepep-plan`依据固定DeepEP v1.2.1低延迟路径，把明确的专家选择编译为离线操作与事件图，用来检查逐专家数据、空专家通知、逻辑QP上下文和计算流等待。输出没有时间戳，不执行GPU、ASTRA或ns-3，不用于报告性能。

完整EP32/E256可显式选择独立输入schema的[流式索引格式](../deepep_indexed/README.md)。默认flat格式及本页数量上限不变。

## 新目录完整流程

仅需Python 3.10以上标准库。从仓库根目录执行以下命令；输出目录/文件须不存在、父目录须存在，计划输出卷预留8GiB。人工示例为四rank、八专家、Top-2，每rank一token，连续执行两次MoE调用。

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout deepep-plan \
  --config examples/deepep_partial_order/input.json \
  --output /dev/shm/my-deepep-plan
PYTHONPATH=python python3 -m moe_ub_cosim scaleout deepep-check \
  --plan /dev/shm/my-deepep-plan --output /dev/shm/my-deepep-plan-check.json
```

正常情况下`all_kernel_completions_reachable=true`。这里的完成是设备侧通信kernel及打包/归并边界，不是Python API返回；`async_finish=False`插入计算流等待，不意味着CPU阻塞。

## 暂扣事件的反例

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout deepep-check \
  --plan /dev/shm/my-deepep-plan --withhold m0.d.count.s0.e4.visible \
  --output /dev/shm/my-deepep-zero-count.json
PYTHONPATH=python python3 -m moe_ub_cosim scaleout deepep-check \
  --plan /dev/shm/my-deepep-plan --withhold m0.c.flag.d0.e2.visible \
  --output /dev/shm/my-deepep-one-flag.json
```

第一条count来自rank 0、指向专家4，token数为零，仍必须发布`-1`。扣住其GPU可见事件会阻塞rank 2的Dispatch，再经Combine通知阻塞所有成员本次Combine完成。第二个只扣住rank 0需要的一条Combine flag：其他rank可开始下一次Dispatch，但完成接收仍需要rank 0参与。

`--withhold`可重复指定精确事件名，只计算无时间依赖闭包，不丢包、不触发重传，也不表示硬件故障。`passed=true`表示计划及依赖检查有效，阻塞结果另见`blocked_kernel_completions`；未知事件名拒绝。

## 输入与约束

`moe-deepep-ll-plan-input/1`不引用外部文件；CLI路径相对当前目录。字段必须齐全，未知字段拒绝。

| 对象 | 字段与限制 |
|---|---|
| 模型 | `experts`为2—256，均分给rank；`top_k`为1—8且不大于E；`hidden_size`为128—8192且为128倍数 |
| 精度 | `dispatch_format=bf16`为`2H+16`字节；`fp8_float32_scales`为`H+4H/128+16`且H为512倍数。Combine固定BF16、`2H`字节 |
| API | `api=compute_stream_wait_no_hook`为默认计算流等待，无hook、无async；专家计算只是无时间前驱阶段 |
| 部署 | `ranks`为2—32，连续等量放置；`cross_rank_path=rdma_only`关闭跨rank P2P；`nics_per_rank=1`只声明逻辑单NIC上下文 |
| 布局 | `max_tokens_per_rank`为1—16，各rank实际token可为0；rank数乘上限须为4倍数 |
| 调用 | `calls`为1—4次MoE调用，`name`唯一，`tokens[rank][token]`给出不重复的Top-K专家，不自动random或复制请求 |
| 预算 | `max_events≤65536`、`max_operations≤16384`、`max_input_bytes≤1MiB`、`max_output_bytes≤16MiB`（三个输出文件总和）；超限拒绝，不截断 |

这些是计划接受的逻辑形状，不证明目标GPU kernel已编译或可运行。调用方提供的选择不自动获得真实来源认证；真实请求须另核验原件hash。

## 输出与完成语义

`input.json`保留原字节；`manifest.json`保存输入/计划SHA-256、记录数和`state=offline_partial_order_only`；`plan.json`包括：

| 对象 | 含义 |
|---|---|
| `operations` | data、count、flag及call/phase、src/dst、expert、原token、贡献数、操作数字节和值；零贡献通知保留 |
| `qp_key` | 跨rank为`[src,dst,local_expert]`，Dispatch/Combine及连续调用共享key；本地为null。不是物理QP号，也不决定NIC端口或ECMP路径 |
| `buffer_epoch/index` | 同一Buffer的Dispatch/Combine从0交替，epoch为2i、2i+1，index为0、1。只标记使用，不证明源缓冲可释放 |
| `events` | `issued`为工作提交；`accepted`为未来传输接口接受目标操作；`visible`为GPU可观察；`sender_done`为发送工作完成，均未映射硬件时间 |
| `deps` | 必须满足的前驱；通知晚于本上下文数据，跨phase保留逻辑QP顺序，同phase多个put的运行时WQE预留顺序不固定 |
| 阶段 | 通信kernel完成等待本地发送已提交及本地需要的通知可见、打包/归并完成；下一次同rank调用等待此前Combine完成，无额外整组完成屏障 |

count/flag的4B是操作数宽度，不是线上包长。`accepted`与`visible`只是待后端实现的接口，不表示已执行RDMA原子或GPU一致性。

计划不执行WQE分裂、队列深度、CQ、重传和源缓冲回收。扣住`sender_done`仍能到达计算完成，仅说明应用图没有直接等待它；真实NIC资源占用仍可能间接阻塞后续提交，不能推断真机丢ACK无影响。不支持hook、异步重叠、多NIC、P2P或动态放置，尚未接入`rdma-run`。

固定源码与已知限制见[语义审计](../../docs/reviews/DeepEP低延迟通信语义与建模修正_20261022.md)。单元检查独立枚举操作关系，覆盖空rank、两专家同目的地、通知与完成反例、跨调用上下文、字节、预算及篡改。`deepep-check`重生成表示并计算闭包，不构成独立源码验证或硬件校准。
