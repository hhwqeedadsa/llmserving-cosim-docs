# 路由来源、观察片段与算子耗时

本目录 `routes/*.json` 和 `profile-test.json` 是人工功能输入；`published-r1-*.json` 仅保存真实来源文件的元数据、摘要和选择清单，原文件留在忽略的 data 目录。人工服务复用 M8 的两组、三层、七请求及完整模型容量，验证来源、窗口和未知目标；真实 R1 对照另验证导入与 routed 专家通信重放。本目录保存这组测试输入、来源依据与实际结果；当前模型适用范围见[模型与验证范围](../../docs/model_validation.md)。

## 输入与索引

| 文件 | 含义 |
|---|---|
| `manifest.json` | `moe-ub-route-manifest/1`：来源、模型/层清单、原始路径及各文件 SHA-256。 |
| `routes/*.json` | `token_layer_map_v1` 的人工输入：外层数组按执行入口排列，每项以层号字符串为键；dense 为 null，MoE 为 Token × Top-K 数组。入口 0 为完整 prompt，入口 1 起每层恰好一行 decode。已用下文两份原文件核验该格式。 |
| `service.json` | `moe-ub-evidence-service/1`，包含请求为空的 M8 服务模板、带摘要的 manifest 引用、逐请求选择范围和空的计算 profile 列表。使用原参考计算。 |
| `profiled-service.json` | 对每组每层提供 `compute_profiles[].file` 摘要引用，使用人工 profile。 |
| `profile-test.json` | `moe-ub-operator-profile/1`，`basis=synthetic_test`，仅检查查表、替换和拒绝行为。 |
| `measure.json` | A100 测量的形状输入；没有人工耗时。H=F=256、Q/KV heads=4/2、head_dim=64、float16、TP=1，共 22 项上下文或专家输入向量。 |

文件引用使用仓库根目录相对路径或绝对路径，必须核对 SHA-256；重复 JSON 键拒绝。真实轨迹来源需要固定数据集修订，公开轨迹未披露的权重/推理软件修订保留 null 并列入 unknown_fields，自行采集则要求两者已固定。模型名称、层数、hidden size、层类型、专家数和 Top-K 必须匹配执行模型；不会将 R1 的数据冒充小型 GQA 模型。

请求显式给出 `file_id`、`first_decode_entry`、`decode_steps`、`target_total_tokens`。每份文件只对应一个请求；范围连续，不跨请求重复消费。入口 i 对应 `decode_index=i-1`、绝对输入位置 `prompt_tokens+i-1`，规范化记录保留文件摘要、入口、层和 Token 行。导入器检查整个文件，缺失层、空 decode、多行 decode、重复/越界专家或 Top-K 不符都拒绝。

## 观察终点与容量

真实目标长度未知时 `target_total_tokens=null`；已知时保持原值。内部原生 Request 的未知目标使用无穷上界比较，输出仍为 null，不导出伪造的有限目标。窗口所需最终输入 KV 为 `prompt_tokens + first_decode_entry + decode_steps - 1`，按 M8 唯一块池预留，已驻留未选中请求也占容量。

最后一个观察步须经过本次实际 ASTRA/UB 完成反馈后才能退出。未达到已知目标的请求记作 `OBSERVATION_ENDED`，不进入原生 Scheduler.done，不写正常请求 latency/end_time；先记录 observation_end，再记录 observation_cleanup 释放片段资源。清理后的容量可供后续观察请求使用，这是一项显式片段工作负载约定，不代表真实服务已完成这些请求。

`service_finished` 仅表示全部声明片段及网络尾部结束。结果另外给出 `requests_completed`、`requests_censored`、`all_requests_completed` 和 `observation_finished`；文件末尾及窗口末尾均不推断 EOS。所报时间是选定层段迭代时间，不能解释为完整请求 ITL/TTFT。

## 计算范围

layer/3 可显式携带 `operator_profile`。表的键是实际 dtype、H/F、attention 维度、TP，以及每 rank 按请求顺序排列的上下文长度、按本地专家编号排列的逐专家输入量（含零）。不使用单请求预检查或模板占位 Token 的形状代替实际调度批次。零工作不查表也不创建计算节点；未覆盖的非零形状拒绝，外推次数为零。

测量实现 `torch_eager_serial_gated_ffn_and_sdpa_v1`：每个非空专家执行三个矩阵乘法及 SiLU/乘法，不同专家串行；每请求执行一个 query 的 GQA SDPA MATH，不同请求串行。输入及权重是固定 seed 的随机张量，权重按 fan-in 缩放，张量分配/初始化不在计时区间。CUDA Event 记录 11 组、每组 10 次重复的平均单次 ns，取中位数向上取整，另有 20 次预热。原始样本、设备/软件、输入/脚本摘要及测量方法一并保存。

专家表值替换 expert_per_input 的线性项；attention 表值加到显式 prepare residual。原 attention_route_pack 数值在启用表时作为未测的准备残差，expert_local_merge、combine_unpack_merge 和同 chip 准备仍为参考。不会叠加旧线性专家耗时，也不会在表中加入网络等待。Python 和 C++ 分别从实际 Token 重算形状、查表结果、中位数、节点耗时与依赖。

该实现不是生产 grouped GEMM、FlashAttention、完整模型前向、AWQ/INT8 算子或目标 NPU profile。相同总输入的不同专家分布可以产生不同时间，方向由实现及测量决定；不能预设“越集中必然越慢”。A100 结果仅对声明算子与形状有效，未测阶段、完整 R1、MLA、CANN 行为及 CM384 校准仍未验证。

## 功能检查入口

在仓库根目录完成环境记录中的 M9 构建后执行，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.route_evidence \
  --manifest fixtures/m9_evidence/manifest.json --output-dir runs/my-route-import

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m9_evidence/profiled-service.json \
  --layer fixtures/m8_composed_service/layers.json \
  --backend build/m9-route-compute-evidence/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json \
  --network fixtures/m3_shared_bottleneck/network \
  --observation fixtures/m3_shared_bottleneck/observation.json \
  --output-dir runs/my-evidence-service

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_evidence_service.py \
  --build-dir build/m9-route-compute-evidence --output-dir runs/my-evidence-check
```

导入命令生成 normalized_decode.jsonl 和 report.json，不自行运行服务或将 EOF 解释为 EOS。服务保留每层来源记录、compute_inputs/compute_evaluation、input_evidence、KV 事件和实际 UB 结果；也可从 M6 场景 YAML 引用新的服务输入。检查脚本核对七请求的 12 次推进、36 条层路由、31744 业务字节，窗口/已知目标/KV 等待/预算/网络降速/被动观测和非法输入，并在通信量相同的 `[4,0]`、`[2,2]` 两个负载上核对真实计算持续时间。

## A100 实测与复查

本机隔离的 `.venv/m9-profile` 已安装 PyTorch 2.8.0+cu126。测量脚本要求 CUDA，不下载模型或回退 CPU：

```bash
PYTHONPATH=python .venv/m9-profile/bin/python scripts/measure_operators.py \
  --input fixtures/m9_evidence/measure.json --output runs/my-a100-profile.json --device 0

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_evidence_service.py \
  --build-dir build/m9-route-compute-evidence --output-dir runs/my-a100-evidence-check \
  --measured-profile runs/my-a100-profile.json
```

2026-10-03 实测文件为 `runs/v03-route-compute-evidence/a100-profile.json`，对应复查在同目录 evidence-final，共 15 次执行、8 项预期拒绝通过。两组层段使用实测表后结束于 3608806 ns，业务仍为 124 次写、31744 字节；七请求均只退出观察。另两次等总量对照中 `[4,0]`、`[2,2]` 的实测及仿真专家节点时长分别为 52327、102196 ns，通信均为 6144 字节。原始测量含 eager 发射间隙，不能视为纯矩阵内核时间或生产库性能；网络与未测阶段仍保留参考状态。测量文件、样本和软件安装报告保留在忽略目录，未提交 Git。

## 已取得的真实 R1-AWQ 轨迹

用户在本机登录并取得访问后，从 [数据集固定修订](https://huggingface.co/datasets/core12345/MoE_expert_selection_trace/tree/27febb7b2d24169560a9f6f83d38eb53f94916cf) 下载 cognitivecomputations/DeepSeek-R1-AWQ/mmlu/abstract_algebra 下的 0.json、1.json，以及数据卡，共约 4 MB。凭据仅用于该 Hub 的认证请求，没有写入源码、日志或 Git。未下载模型权重或整个数据集。

`published-r1-manifest.json` 固定这两份原文件的路径与 SHA-256；`published-r1-selection.json` 固定数据卡、[R1 结构配置](https://huggingface.co/deepseek-ai/DeepSeek-R1/blob/56d4cbbb4d29f4355bab4b9a39ccb717a14ad5ad/config.json)摘要及检查片段。结构配置只是 H=7168、61 层、前三层 dense、256 routed 专家和 Top-8 的依据，不是发布者采集时的权重版本。数据卡报告 AWQ 4bit，实际采集权重/软件修订、设备、激活精度和 EOS 原因仍未知；它不是 CM384 INT8 同条件路由。

两文件均为 129 个外层入口，prompt 分别为 26、37 个 Token，随后各有 128 个 Decode 入口。逐个检查全部 prefill/Decode 记录的层清单、行数、8 个互异专家及 0～255 范围。CLI 导出的 `2×128×58=14848` 条 Decode 层记录与独立读取原文件的结果完全一致，不把入口 0 当 Decode，不由文件末尾推断 EOS。

小型执行选择层 3、4、5：请求 801 消费文件 0 的入口 1、2（绝对输入位置 26、27）；请求 802 消费文件 1 的入口 3、4（位置 39、40）。两请求同在 rank 2，组成两个固定批次；专家按 `(expert_id + layer_id - 3) % 3` 放到 rank `[2,7,12]`，保留原始专家编号和全部 96 份专家输入。向量为显式参考的 float16、14336 字节，fabric chunk=4096，发送槽=2；同 chip 路径沿用 M8 声明。

测试驱动只在既有 cosim/1 完成边界提交 M8 multilayer-plan/2，不另建网络或调度器。两轮在同一 UB 网络中执行，第二轮由第一轮真实 WAVE_DONE 推进。四次执行（来源开、关、不挂观测、1 Gbit/s 降速）均通过：基准 78 次写、315392 fabric 业务字节、12 次本地交付，边界为 0/147462/270334 ns；降速后为 0/1425546/2614912 ns。来源开关不改执行，逐记录消费、逐专家输入、业务矩阵、实际收发回调、依赖、原生队列及结束排空分别核对。重复源文件、越界窗口和错误模型维度均拒绝。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_published_routes.py \
  --build-dir build/m9-route-compute-evidence \
  --output-dir runs/my-published-route-check
```

该命令要求清单中已获准下载的文件位于指定 data 路径且摘要一致，缺失时明确失败，不自动登录或下载。实际结果在 runs/v03-route-compute-evidence/published-routes。此重放不是完整 R1 服务，不验证其 MLA、shared expert、完整权重/KV 容量或生产到达过程；其模型计算使用声明参考值。A100 小算子表因维度不同未用于 R1。原生 Scheduler、观察窗口及唯一 KV 池已在上文配置服务检查中验证，不能据此宣称完整 R1 的组合运行已完成。
