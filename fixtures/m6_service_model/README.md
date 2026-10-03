# M6 服务模型与容量参数样例

`moe-ub-configured-service/1` 使用显式的 `moe-ub-model/1` 结构与逐 rank 字节预算，执行一组 EP 的单个选定 MoE 层。这里两份模型均为人工构造的功能输入，没有模型下载、真实路由、设备测量或目标部署校准。

## 输入及边界

`model_spec` 声明名称、来源、TP=PP=1、隐藏维度、词表、上下文上限、注意力头/KV 头/头维度、五种 dtype、embedding/输出头是否共享，以及从零连续编号的完整层清单。当前结构为 `bias_free_gqa_rmsnorm_gated_ffn`：无 bias 的 GQA（KV 头数等于 Q 头数时为 MHA）、两个 RMSNorm 和三矩阵门控 FFN。MoE 层另外声明路由专家数、Top-K、放置与共享专家数量/维度；共享专家权重在各 rank 复制，路由专家权重只在放置指定的 rank 计入。

权重、计算、Dispatch 和 Combine dtype 接受 float16、bfloat16、float32；KV 另接受按每元素一字节核算的 fp8。dtype 不产生量化/转换计算，FP8 也未计设备实现特有的 scale/对齐开销。MLA、量化权重、bias 等其他结构明确拒绝，不能套用该公式声称 R1 可部署。

`groups` 当前只允许一组，成员、放置和映射与 layer 输入一致。`memory.ranks` 按成员顺序给出 `device_bytes`、`workspace_bytes`、`communication_buffer_bytes`；`capacity_scope=rank_endpoint` 表示独立计算端点的容量，不能把整芯片容量给每个 die 重复填写。`max_kv_blocks_per_rank` 是块池对象数量的主机资源预算，计算出的块数超限时拒绝，不截断可用容量。

`scheduling.max_num_seqs=1` 是本步执行边界；`max_num_batched_tokens` 为正整数，针对一轮 Token 数，不与请求总输出长度比较。每个 ready-KV 请求的 `decode_routes` 显式列出剩余步骤；输入继承旧功能服务的绝对输入位置口径：第一个 Token 索引为 `prompt_tokens + generated_tokens - 1`。这些路由始终标记为合成数据。

完整模型用于容量核算，执行图只覆盖 layer 文件选中的 MoE 层；其 `next_layer` 必须为零，选定层暂不执行共享专家。模型清单中的稠密层及其他 MoE 层保留自身类型和放置，只计容量，未变成执行图。`input_evidence.json` 和 manifest 明确列出执行层、未执行层与省略的 embedding、final norm、输出头及采样，时延只表示选定层的迭代。

## 容量和生命周期

令 `H` 为隐藏维度、`Q/K` 为注意力/KV 头数、`D` 为头维度、`V` 为词表、`L` 为完整层数、`W` 为权重元素字节数：

- 各 rank 复制 embedding `V×H×W`、final norm `H×W`，未共享时另计输出头 `V×H×W`。
- 每层共享的两个 norm、QKV 投影及 O 投影分别为 `2×H×W`、`H×(Q+2K)×D×W`、`Q×D×H×W`。
- 稠密 FFN 为 `3×H×F×W`。MoE router 为 `H×E×W`，路由专家为 `该 rank 实际专家数×3×H×F×W`；共享专家另按其数量和维度计算。
- 每本地请求每 Token KV 为 `2×K×D×L×KV元素字节数`，不除以 EP。整块数为 `(设备容量−权重−工作区−通信缓冲区) // KV块字节数`，余数单独记录。

通信缓冲区下界保守地取所有可能的远端输入/返回向量总量与双向发送槽分块容量的较大值：`max(R×min(TopK,R−1)×H×(Dispatch元素字节数+Combine元素字节数), 2×send_slots×chunk_bytes)`。工作区是显式参考预留，尚未由实际内核测量确定。

接纳前按 `ceil((request.output−1)/block_size)` 在 Scheduler 使用的同一个原生块池中预留；预留不增加 computed/reached Token，不产生 KV 网络流量。原 `add_decode()`、每轮 `schedule()` 和 `add_done()` 复用该块池，最终释放一次。权重和静态预留保持驻留，服务结束要求所有请求 KV 释放。

尚未接纳的请求保存在前端队列，其 ready-KV 是外部给定输入，尚未声明在目标 rank 驻留。当前每 rank 只接纳一个请求；排队请求等待前一请求结束释放后进入。已驻留的多请求排队、连续组批和因当前空闲块不足而等待的组合能力留到 M7，本样例不把序列槽等待称为多请求容量等待。单请求最终容量永久不足则在后端启动前报错。

容量初始化位于主仓库 `configured_scheduler.py`：锁定的 Scheduler 构造器直接创建均分权重 MemoryModel，故本适配只替换初始化，继续使用其原 `schedule/add_decode/add_done`、Request、BlockPool 和 TieredKVCacheManager。没有修改依赖、全局 monkeypatch 或临时虚假模型。

## 独立预期与实际执行

| 配置 | 完整模型与所执行层 | 逐 rank 权重 | KV 块/容量 | 实际业务 |
|---|---|---|---|---|
| gqa | H=64，三层：dense、MoE、带共享专家的 MoE；执行第 1 层 | rank 2/7/12：255872 / 280448 / 231296 字节 | 384 字节/Token，4 Token/块，容量 3/3/2 块 | 6 轮、24 次写、4608 字节、102924 ns |
| mha | H=32，单 MoE 层，FP32 权重、共享 embedding/head、FP8 KV 字节核算；执行第 0 层 | rank 4/10：74496 / 49920 字节 | 64 字节/Token，8 Token/块，容量 2/2 块 | 2 轮、4 次写、256 字节、22192 ns |

GQA 的公共 embedding/norm/head 为 32896 字节；dense 层为 73984 字节；执行层公共部分为 25472 字节、每专家 36864 字节，专家数 1/3/1；第三层公共部分为 37504 字节、每专家 24576 字节，专家数 2/0/1。上述各项相加得到表中非均匀权重。KV 使用所有三层。

GQA 中请求 101 在 rank 2 执行四步，请求 102 在 rank 7 执行三步，rank 12 只有专家工作。每步 101 对远端 rank 7 贡献一份输入/返回，102 对远端 rank 2 和 12 各贡献一份；两者前三轮每轮 6 次写、1152 字节。第四轮仅 101 为 2 次写、384 字节。请求 103 在 1000 ns 到达 rank 2，等待 101 结束后执行两步，每步 2 次写、384 字节。

实际还执行 GQA 来源开/关/不挂观测、MHA 最后一输入恰好占一块、空服务和九项预期拒绝。Python 单元独立核对完整容量与块边界；集成测试核对实际节点映射、ASTRA 依赖、UB data_ready/send_done、回收和来源被动性。

已有环境的构建方法见 [环境记录](../../docs/environment.md#2026-10-03m6-服务模型与容量参数化)。从根目录运行以下入口，输出目录须尚不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m6_service_model/gqa-service.json \
  --layer fixtures/m6_service_model/gqa-layer.json \
  --backend build/m6-service-model-capacity/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json \
  --network fixtures/m2_single_layer/network \
  --output-dir runs/m6-model-gqa

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_service_model_capacity.py \
  --build-dir build/m6-service-model-capacity \
  --output-dir runs/m6-model-check
```

旧 `moe-ub-service-fixture/1`、数据、多层、P/D schema 保留原模型与范围，未自动迁移到新配置。当前入口使用 `service` CLI；完整场景 YAML 和新的模型容量 schema 尚未组合到研究导出入口。


后续部署本地性任务已为该配置服务增加 layer v2：group 同时声明全局 die/chip/server 归属及本地准备时间，后端按固定部署区分本地交付与 UB 写。模型容量仍按独立 rank 核算，详见[本地性样例](../m6_locality/README.md)。本目录的原 layer v1 输入继续保持原行为。
