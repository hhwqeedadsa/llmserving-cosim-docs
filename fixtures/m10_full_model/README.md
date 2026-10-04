# M10 完整 Decode 参考计算图

本入口将 model/2 中每个块和首尾阶段放入同一轮 ASTRA 图，并使用已有 CANN 行为、真实 UB 完成事件、原生 Scheduler 和唯一 KV 池。执行的是参考耗时图，没有运行 MLA、量化矩阵或文本生成内核。

`service.json` 使用 `moe-ub-configured-service/4`，各组增加 `full_model_profile`；`layers.json` 仍用 layer-segments/1，按模型顺序提供**所有 MoE 层**的 CANN layer/3 输入。稠密层由模型清单生成，不提供专家路由。`evidence.json` 的内层服务也采用 /4，复用上一容量任务的七份人工轨迹文件，以本目录 manifest 的固定摘要记录来源；不是新的真实模型采集。

当前限 TP=PP=1、KV 已就绪的 Decode、含 MoE 的 MLA model/2、CANN BF16 计算/KV/Combine 与 INT8 Dispatch。旧 configured-service/1–3 及其单层/层段含义不变，旧层段入口仍拒绝选中的 shared expert。

## 阶段与成本

每个 rank 只对本地批次 Token 执行以下阶段；本地没有 Token 时不创建这些 COMP 节点，但仍处理别处发来的专家工作与状态消息。

```text
embedding
  → 对每个块：attention_norm → mla_prologue → mla_attention → mla_output
              → attention_residual → ffn_norm
              → dense_ffn 或以下 MoE 分支
              → ffn_residual
final_norm → lm_head → sampling

MoE 的 ffn_norm
  ├→ router → Dispatch 量化打包 → CANN Dispatch/专家/Combine
  └→ shared_expert
两路全部完成 → shared_routed_add → ffn_residual
```

`mla_prologue` 合并表达 Q/KV 投影、LoRA norm、RoPE 与本轮缓存写入的参考成本；`mla_attention` 表达压缩上下文注意力和 value 展开的参考成本；`mla_output` 为输出投影。它们是可单独审计的阶段，不是按数值执行的算子。共享 gated FFN 也用阶段成本表示。

cost/1 的 `basis` 必须为 `declared_reference`。`model_structure` 必须逐字段匹配模型，仅排除各层 `expert_placement`，因此隐藏维度、注意力/KV 结构、层型、权重/激活精度变化后不能静默沿用原成本声明。每组可以明确声明不同成本；没有自动迁移或硬件拟合。

| 字段/阶段 | 本轮实际时长 |
|---|---|
| 首尾和逐块 `per_token_ns` | 本地批次 Token 数 × 对应成本 |
| `mla_attention` | 本地各 Token 的 `(token_index+1)` 之和 × `attention_per_context_token_ns` |
| routed 专家 | 实际送到本 rank 的专家贡献数 × `routed_expert_per_input_ns`；Top-K 同目的 rank 的多个专家分别计入 |
| `shared_expert` | 本地 Token 数 × shared expert 数 × 单专家成本 |
| CANN 局部处理 | 沿用固定 CANN 模板的计数检查、重排、结果打包、局部交付和归约成本 |

正工作阶段要求正成本；模型没有共享专家时，`shared_expert` 与 `shared_routed_add` 两项成本必须为零，也不创建对应 COMP。CANN 子层的 `attention_route_pack` 仅承载 `dispatch_quant_pack`，不能再次计入注意力/router；`expert_local_merge=next_layer=0`，Combine 的 CANN 状态检查和加权归约只计一次。

所有 COMP 仍竞争每 rank 的一个计算槽。共享专家可在路由分支等待网络时运行，也会阻塞本地尚未执行的专家计算或归约。因此加长共享专家不保证它是最后结束的分支；两路依赖和真实资源排队共同决定完成时间。层间只有 rank 本地依赖，未增加全 rank 逐层屏障。

## 执行和检查

已验证的本机命令如下，输出目录必须尚不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m10_full_model/service.json \
  --layer fixtures/m10_full_model/layers.json \
  --backend build/m10-full-model/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json \
  --network fixtures/m3_shared_bottleneck/network \
  --output-dir runs/full-model-example --max-simulation-ns 4000000
```

完整图为 `moe-ub-full-model-plan/1`：`executed_layer_ids` 含全部块，`communication_layer_ids` 只含 MoE；子层保持独立可检查的 CANN 计划。每个 rank 的 `model_stages` 记录批次、上下文、成本和依赖，`layer_phases` 只记录 MoE 通信子层。Chakra 与计划逐节点核对，C++ 独立按模型/成本重建图，并检查整次服务中 model_spec/profile 未变。新 deployment/5 固定这些声明。

只有整轮所有 rank 的图都完成，原生 Scheduler 才推进一个 Token；sampling 表示阶段耗时，停止条件仍来自已知请求预算或观察窗口。操作/端口公共观测仍只使用 Dispatch、Combine；新增计算阶段不伪装成公共通信操作。

两组三 rank、四块模型的基准共五轮，305 次 UB 写，120316 B 载荷，其中 113664 B 为业务数据。完整图完成于 131279 ns。它与旧层段使用不同的显式阶段成本，不能把二者时间差解释为模型加速。慢共享专家、慢 sampling、1 Gbps 链路、三块 KV 容量、不同组 Token 预算、窗口结束、来源开关和 scenario 入口均有对照。

另用 EP=3、六专家、H=1024 的合成模型实际执行了 61 块（前三块 dense、后 58 块 MoE）：3102 个节点、986 次交付，其中 754 次真实 UB 写。还执行了 MoE/dense/MoE 和无共享专家结构。这些检查没有采用 R1 的维度、256 专家、128 rank 或 CM384 网络，也没有提高 4096 交付上限。

成功运行且至少完成一轮完整图才记录 `full_model_graph_execution_validated=true`，空请求或失败运行保持 false；保留 `full_model_execution_validated=false` 表示尚无完整目标模型/数值内核运行验收，另有 `numerical_model_executed=false`、`target_deployment_validated=false` 和 `unvalidated_reference`。时延是已就绪 KV 的完整声明 Decode 迭代参考时间，不能当作原始请求 TTFT 或目标硬件实测 ITL。

检查脚本见 [完整服务检查](../../tests/integration/check_full_model.py)、[结构边界检查](../../tests/integration/check_full_model_structure.py)；实际命令、失败原因、日志和回归结果见 [环境记录](../../docs/environment.md)。
