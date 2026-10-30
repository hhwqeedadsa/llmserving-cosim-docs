# 完整EP32操作计划：流式索引格式

该入口保留固定DeepEP v1.2.1低延迟路径的全部逐专家数据和通知，用于准备后续网络接入。示例为人工EP32、256专家、每rank8专家、4个单token/Top8、一轮Dispatch与Combine；没有真实路由来源认证、网络、GPU或时延预测。EP32是通信组大小，不是已执行32端点网络；后续实际网络仍采用用户指定的一种128/256/512端点Clos。

在仓库根目录使用已有基础Python，不需要前端环境或编译：

```bash
mkdir -p runs/my-indexed
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-plan \
  --format indexed-jsonl --config examples/deepep_indexed/input.json \
  --output runs/my-indexed/plan
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-check \
  --plan runs/my-indexed/plan --output runs/my-indexed/checked.json
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout deepep-check \
  --plan runs/my-indexed/plan --withhold m0.d.count.s0.e8.visible \
  --output runs/my-indexed/zero-count-withheld.json
```

输出目录和检查文件必须为新路径，父目录须存在。输出卷预留8GiB。首次命令生成16,448操作、66,144事件；第二条应全部可达，第三条显示一个零token通知未可见时阻塞的kernel完成。`--withhold`只是无时间依赖检查，不是丢包；最多256个、每个最多256字符。

调整输入时，修改`model`、`execution`和`calls`，其语义与[原计划](../deepep_partial_order/README.md)一致：2..32 ranks、2..256专家且均匀分配、Top1..8、每rank0..16 token、1..4 calls、BF16或规定布局的FP8。`max_tokens_per_rank`仍需满足原来的对齐条件；支持计划形状不等于真实CUDA kernel已验证。原格式仍为默认`--format flat`，使用原input/1及原数量限制；两个输入schema不能混用。

| limits字段 | 限制对象 | 硬上限 |
|---|---|---:|
| `max_logical_operations` | 全部操作，包括零通知 | 98,304 |
| `max_logical_events` | 全部事件 | 394,624 |
| `max_index_bytes` | 事件ID映射、键、序号及闭包的Python浅层核算 | 64MiB |
| `max_input_bytes` | 原输入文件 | 1MiB |
| `max_output_bytes` | 五个输出文件合计 | 16MiB |

逻辑数量硬上限来自既有最大输入维度，不是常驻数组容量。输出和索引限制独立，不能保证满维度输入都可输出；禁止通过删除通知绕过预算。索引核算不是进程全部内存：阶段矩阵、前一QP标记和数据关系仍需保存，实际运行应另设RSS、时间和磁盘限制。单行硬上限64KiB。预检通过后才建输出目录；若写入被中断，缺少最后的`manifest.json`，应保留失败记录并换新目录，不能视为成功计划。

| 文件 | 内容 |
|---|---|
| `input.json` | 原输入字节 |
| `plan.json` | 语义范围、列定义、文件行数/hash、汇总及存储说明 |
| `operations.jsonl` | 每行一个操作数组，按`operation_columns`解释 |
| `events.jsonl` | 每行一个事件数组，按`event_columns`解释 |
| `manifest.json` | 输入/计划/两份JSONL的完整性清单，最后写入 |

行号从0开始。事件的`deps`引用更早的事件行，`operation`引用操作行或为null。操作的`events`按`issued/accepted/visible/sender_done`引用四个事件行；事件`attributes`保留buffer等原属性。两个文件共同表达部分顺序，文件排列不是实际WQE提交顺序。`qp_key`只是逻辑上下文，4B通知操作数不是线上包长。

检查器从原输入逐项重生成并比较，每次仅读一行；因此能拒绝重新计算哈希后的内容修改，但不能认证输入来自真机或证明源码模型准确。原始ID保留，可直接用于`--withhold`。同一输入重复五文件应逐字节相同；整个目录可迁移，不包含指向旧运行的路径。

该格式没有接入`PersistentStreams`、远端事务、WQE/CQ或源buffer寿命检查，不自动应用旧库“四条put发布”策略，更不能套用为现代GIN/direct。其收益是保留完整语义并减少表示重复；网络性能和论文创新需后续独立验证。
