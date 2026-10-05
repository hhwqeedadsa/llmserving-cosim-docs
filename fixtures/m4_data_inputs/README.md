# 数据导入与逐项替换

此入口核对实际 Mixtral 配置的完整容量，并在指定的第 7 层上执行三个连续解码步骤。层号从零开始；其余 31 层没有执行，结果中的迭代时延不代表完整模型 ITL、TTFT 或吞吐。

三份配置使用同一模型、EP4/TP1/PP1 放置、四个请求、网络、分块和容量，仅按以下顺序替换数据：

| 配置 | 路由 | 计算 | 实测写次数 / 字节 | 完成时间（ns） |
|---|---|---|---:|---:|
| model-stage.json | fixed.jsonl，每步重复相同选择 | 固定功能参数 | 42 / 344064 | 64738 |
| route-stage.json | continuous.jsonl，三个步骤分别给定选择 | 同上 | 28 / 229376 | 54900 |
| compute-stage.json | 同上 | 六项历史 H100 FP16 算子记录及其余参考耗时 | 28 / 229376 | 303633 |

两份路由都是手工合成输入，没有执行模型前向，也不是实际数据集提取的专家选择。`kind=synthetic` 写入运行清单；本目录不提供真实路由验证；后续真实来源见[路由证据用例](../m9_evidence/README.md)。原 P/D 用例继续使用其命名的小模型和合成路由。

## 使用

在仓库根目录，使用已验证的 Python 环境及 M4.2 后端：

```bash
env PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m4_data_inputs/compute-stage.json \
  --layer fixtures/m2_single_layer/layer.json \
  --backend build/m4-pd-queue/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json \
  --network fixtures/m2_single_layer/network \
  --output-dir runs/m4-data-example --max-simulation-ns 5000000
```

输出目录必须尚不存在。`input_evidence.json` 保存完整模型审计、规范化路由和计算行依据；`graphs/g*/plan.json` 将实际消费记录附在 `input_provenance` 中。每条传输已有的 `source_record_index` 是该图 Token 列表的下标，据此对应同序的 `route_records`，其中另存文件行号及原始索引。它们属于输入与内部对应关系，不进入端口观测，也不影响业务包。

三阶段、来源开关、重复运行与错误输入检查：

```bash
env PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_data_inputs.py \
  --build-dir build/m4-pd-queue --output-dir runs/m4-data-check
```

实际命令和记录见[历史环境记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/environment.md#2026-09-29m4-3-data-inputs)。

## 模型和容量

模型文件来自固定 LLMServingSim 的 `configs/model/mistralai/Mixtral-8x7B-v0.1.json`，由路径和 SHA-256 确认；模型类型、32 层、八专家 Top-2、4096 隐藏维度、14336 专家中间维度、32 个注意力头、八个 KV 头及 128 head_dim 分别检查。按其 Mixtral 架构逐层列出 MoE 位置，没有将所选层替换成全部模型。

每 rank 持有完整的共享权重和各层两个专家。FP16/BF16 均为两字节；本用例从第一阶段即显式选择 FP16，原配置的 BF16 保存在记录中，计算阶段不再隐式改变 dtype。

每 rank 全模型权重 25,759,850,496 字节；完整 GQA KV 为 `2 × 8 × 128 × 32 × 2 = 131072` 字节/Token。每设备声明 80 GiB，预留 1 GiB 工作区和 1 MiB 通信缓冲，剩余容量为 28164 个 16-Token KV 块，另有 516096 字节不能组成整块。独立算式与原生 MemoryModel/BlockPool 相符，预留实际扣入块池容量。

通信缓冲下界覆盖当前四 Token 图的向量总量及发送槽分块。工作区预算是给定假设，未从目标设备测量；容量相符不等于验证了实际设备可部署性。每个请求在启动前检查剩余全部计算输入所需容量，每次决定及结束记录实际 KV 使用。

## 连续路由约定

当前适配器仅处理一个所选层、一个 EP 组的解码片段。文件引用包含路径和 SHA-256，相对路径按仓库根目录解释；外部数据可以使用绝对路径。每行必须包含 `moe-ub-route-source/1`、字符串 request_id、decode phase、source_token_index、layer_id、expert_ids 和 source_index。

`source_token_index` 是从零开始的完整输入序列位置。规范化公式为 `token_index = source_token_index - prompt_tokens`：首个生成 Token 作为解码输入时索引为零。给定已有 `generated_tokens` 个输出的片段，从 `generated_tokens - 1` 开始，连续消费 `decode_steps` 条；不会把任意源文件第一行强行视为解码零。`source_index` 是原提取记录的唯一非负编号，`source_line` 是当前文件从 1 开始的行号，两者均保留。

所有请求与所选层的记录必须完整且恰好覆盖片段；缺失、重复、错误层、错误 phase、非法专家、摘要不符均报错。不补齐、不重复抽样、不复制其他层路由。未来导入实际记录时必须声明 kind=measured、准确来源说明及正确转换；该标记仅声明来源，不自动证明目标硬件或路由真实性。

## 计算引用的适用范围

读取固定仓库 `profiler/v0/perf_models/H100/mistralai/Mixtral-8x7B-v0.1/tp1/layers.csv`。审阅 `profiler/v0/profiler/layers/main.py` 确认 profiler 显式采用 float16、从配置构造模型而不装载 checkpoint；CSV 没有逐次硬件环境、层号或权重标识。因此只将它作为形状相关的历史参考，不作为目标 NPU 校准。

引用 input=1、kv_cache=0、tp_size=1 的 input_layernorm、q_proj、k_proj、v_proj、o_proj、post_layernorm，源 CSV 行为 3/4/5/6/8/9，共 82911 ns。这些算子不读取 KV 上下文，输入形状为 `[1,4096]`，K/V 输出为 `[1,1024]`，其余为 `[1,4096]`；当前调度每个活跃 rank 恰好一个 Token。记录 dtype、形状、单位、文件摘要及行号；错误 dtype、缺行和重复键拒绝，不插值、不外推。

prepare 整段替换为上述总和加显式的 5000 ns 剩余参考耗时，共 87911 ns；不另保留旧 prepare 节点，也不再增加本地访存耗时。attention、RoPE、路由及打包、专家、局部合并、Combine 处理和 next-layer 占位功能计算仍为参考值。未引用 TP4 attention 数据，未按算力比例换算 NPU。

`calibration_status` 和通信模板状态均为 `unvalidated_reference`。本步骤验证数据导入、容量及逐项替换的因果关系；多层执行与目标源码核对分别见[多层用例](../m5_multilayer/README.md)和[模型核对](../m5_model_audit/README.md)。
