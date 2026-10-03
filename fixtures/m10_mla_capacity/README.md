# MLA 容量合同与参考计算夹具

本步增加 `moe-ub-model/2`，独立描述 MLA 权重存储与压缩 KV。`model/1` 的 GQA 字段和数值不变。审计结果用于原生 Scheduler 的同一个 KV 块池；没有新增调度器、模拟块池、后端协议或量化算子。

## 输入与核算

顶层延续 `name/source/hidden_size/vocab_size/max_position_embeddings/tp/pp/tie_word_embeddings/layers`，架构必须为 `bias_free_mla_rmsnorm_gated_ffn`，TP=PP=1。`source.kind` 为 synthetic 或 declared_reference。MLA 注意力字段为：

| 字段 | 含义 |
|---|---|
| num_heads | N，注意力头数 |
| q_lora_rank / kv_lora_rank | Q / C，query 与 KV 压缩维度 |
| qk_nope_head_dim / qk_rope_head_dim | Dn / Dr，非旋转与旋转维度，Dr 必须为正偶数 |
| v_head_dim | Dv，value 头维度 |

不要求 `H=N*Dn`，也不接受 GQA 的 num_kv_heads/head_dim。所有维度为正整数。五个投影按输出×输入依次为 `Q×H`、`N(Dn+Dr)×Q`、`(C+Dr)×H`、`N(Dn+Dv)×C`、`H×NDv`；每层另含两个 H 维 block norm、Q/C 维 LoRA norm。dense/routed/shared FFN 均为两个 F×H 和一个 H×F；shared 专家在每个 rank 复制，routed 按各层实际 placement 计数，允许非均匀放置。

MoE 层在旧结构字段之外必须提供布尔 `router_correction_bias`，决定是否计入 E 维路由修正向量；router 为 E×H。完整容量始终包含未执行层。embedding、独立 lm_head 均为 V×H，final norm 为 H；绑定输出头只计一次，绑定两方存储政策必须相同。没有 MTP 或额外 bias 的隐式权重。

`weight_storage` 必须逐项声明 mla、dense_ffn、routed_experts、shared_experts、embedding、lm_head、norm、router、router_correction。每项字段如下：

| 字段 | 可接受的含义 |
|---|---|
| dtype | float16/bfloat16/float32；前四类矩阵另可选择 int8 |
| quantization | 浮点只能 none；INT8 只能 symmetric_per_output_channel |
| scale_dtype | 浮点必须 null；INT8 必须 float32，每个输出通道一个 scale |
| zero_point / padding_bytes | 本合同均要求整数 0，不接受布尔值；没有额外零点张量或存储填充 |

审计逐 tensor 输出 shape、独立副本 count、parameter_count、tensor_bytes、scale_bytes、weight_bytes；后者等于张量与 scale 之和。向量、router 和 embedding/head 暂不接受量化；不支持其他 packing、scale 轴或 scale 精度，不能只改 dtype 沿用本算式。

`dtypes` 只包含 compute、kv、dispatch、combine，不包含统一 weights。compute/kv/combine 可为 FP16/BF16/FP32；dispatch 另可声明 INT8，实际运行仍必须满足模板限制。CANN 模板要求 BF16 compute/KV/combine 和 INT8 dispatch。权重存储类型独立于运行成本、激活与线路类型。

`kv_cache` 必须是 `layout=absorbed_latent_rope`、`quantization=none`、`padding_bytes_per_token=0`。latent 与共享 RoPE 使用同一显式 kv dtype，不按头数复制：

```text
KV/token = 模型层数 × (C + Dr) × sizeof(kv dtype)
KV/block = KV/token × memory.block_size
non-KV = 权重张量 + scale + workspace + communication_buffer
capacity = floor((device - non-KV) / KV/block)
```

RoPE 表、临时展开/反量化、运行时元数据属于显式 workspace 预算。memory 沿用独立 rank_endpoint 预算和 host 块数上限；重复 rank、缺项、不足一个块、整数溢出、超出 host 块预算均拒绝，不截断。完整运行中的组成员/物理端点校验沿用现有路径。

## 容量与执行分别使用

`capacity_audit` 接受 `moe-ub-capacity-input/1`，字段为 model_spec、rank_ids、memory，可离线核算 dense/shared 在内的完整库存，不创建任何池、执行图或网络对象。通信缓冲区是条件预算，输出 `communication_budget_basis=declared_not_graph_validated` 和 `execution_scope=capacity_only`，不代表完整图缓冲已验证。输出文件必须不存在，父目录须存在：

```bash
mkdir -p runs
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.capacity_audit \
  --input fixtures/m10_mla_capacity/r1-capacity.json --output runs/mla-r1-capacity.json
```

`r1-capacity.json` 只描述完整 R1 的条件存储，不是运行输入。结构、来源与固定摘要继承 [目标参考合同](../m10_target_contract/reference.json)及[就绪核对](../../docs/target_readiness.md)。128 个 rank、每 rank 两个 routed expert、每 MoE 层一个本地 shared expert，3 dense+58 MoE；INT8 矩阵/FP32 scale、BF16 embedding/head/norm、FP32 router/修正。独立前步审计与新实现的全部 19 类 tensor 形状、副本、参数与字节逐项匹配：

| 每 rank 条件结果 | 数值 |
|---|---:|
| 权重张量 | 24,400,011,264 B |
| scale | 24,624,384 B |
| 总权重 | 24,424,635,648 B |
| BF16 latent+RoPE KV/token | 70,272 B |
| 16 Token 块 | 1,124,352 B |
| 64 GB 减去权重、4 GiB workspace、3,954,809,344 B 通信预算后的完整块数 | 27,861 |

这些数值不表示实际加载 checkpoint、NPU 校准或目标部署容量极限。原始 FP8、公开轨迹 AWQ4 与这里的参考 INT8 存储方案分别记录。

运行入口仍为既有 configured/composed/evidence service。`service.json` 使用 `synthetic/mla-capacity-reference-1024`，执行现有 CANN 连续三层 MoE 图，计算为显式参考阶段成本。复用原始 CANN layers.json；MLA 库存有四层，dense 0 只计容量。完整 dense、selected shared expert、全模型执行仍拒绝；GQA/A100 operator_profile 在形状查表前明确拒绝用于 MLA。证据与 manifest 标记 `mla_kernel_validated=false`、`quantized_kernels_executed=false`、`full_model_execution_validated=false`，不把存储精度等同于 INT8 算子执行。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m10_mla_capacity/service.json --layer fixtures/m10_cann/layers.json \
  --backend build/m10-cann-behavior/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m3_shared_bottleneck/network \
  --output-dir runs/mla-service
```

把 input 换为 evidence.json 则运行明确停止的观察窗口。routes/ 是本仓库合成 ID 序列，由 M9 人工夹具将专家 3 改为 5，保留 dense null 和 prompt/解码索引，使观察过程包含跨芯片业务；manifest 固定逐文件摘要并标记 synthetic_test，不是采集或真实模型路由。

## 独立预期与实际验证

`toy-capacity.json` 用 H=8、V=16、N=2、Q=4、C=2、Dn=Dr=Dv=2，两层分别是 dense F=6 和 MoE F=4。3 个 routed 专家按 [2,7,7] 放置，shared F=2、每 rank 一个，untied embedding/head。投影及 norm 每层为 48+64+48+48+64+32+8+4=316 B；dense FFN 为 224 B；每个 routed 专家 160 B；shared 96 B；router/修正 108 B；embedding/head/final norm 共 528 B。结果为 1748/1908 B，scale 448/512 B；KV 每 Token 16 B，每四 Token 块 64 B。浮点 KV、绑定头、未量化矩阵及非法合同另有独立常数预期。

实际小模型每 rank 权重为 10,408,520 B、KV/token 384 B、block 1536 B；workspace 4096 B、三层通信预算 6,304,512 B。默认八块，收紧到三块时 A/B 不能同时驻留，释放 A 后接纳 B；逐请求 ceil 的两份 2 块不会合并成 3 块。相同 device 预算中只将 MLA 投影的 BF16 存储改为 INT8+scale，可用块从 3 变为 1849，实际首轮从 A 变为 A/B，参考计算成本不变。

`tests/integration/check_mla_capacity.py` 用现有 UB 构建执行七种对照，检查实际事务/节点完成、72 份贡献、容量占用与回收，以及未被选入 batch 的驻留请求继续占 KV、窗口退出无 EOS。基准 305 次写、120316 B 总载荷、113664 B 业务、131530 ns；三块预算为 377 次写、143142 ns（轮次增加带来额外计数消息），业务字节不变。1 Gbps 对照为 1214814 ns，本组下一轮提交随真实完成后移。观察窗口最终夹具传输 61440 B 跨芯片业务，全部七请求按观察结束清理。

102 项单元测试、旧 GQA/连续批处理/组合服务/观察与计算依据/CANN 五套回归通过。新 MLA 输入另实测七项启动前拒绝。命令、失败原因、修复及退出状态见 [environment.md](../../docs/environment.md)。未重建后端、未修改依赖，未运行完整目标场景。
