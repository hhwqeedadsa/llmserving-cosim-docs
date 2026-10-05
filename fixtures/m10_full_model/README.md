# 完整 Decode 图的测试输入与结果

本目录检查把模型清单中的全部块和首尾阶段放入同一轮依赖图。ASTRA 按参考计算耗时和 ns-3-UB 完成事件执行，整轮完成后由原生 Scheduler 推进 Token，KV 使用同一个块池。没有执行 MLA、量化矩阵或文本生成数值内核。

当前模块和阶段说明见[架构](../../docs/architecture.md)，计算成本与容量依据见[模型与验证范围](../../docs/model_validation.md)。本目录保留可运行的小输入和独立预期。

## 输入与覆盖范围

| 文件 | 内容 |
|---|---|
| [service.json](service.json) | configured-service/4，两组三 rank、七个请求，各组声明 full_model_profile。 |
| [layers.json](layers.json) | layer-segments/1，按模型顺序列出全部 MoE 层的 CANN layer/3 输入；dense 不需要专家路由。 |
| [evidence.json](evidence.json) 和 [manifest.json](manifest.json) | 复用容量用例的七份人工轨迹，核对文件摘要与观察结束行为。 |

模型为合成 MLA，TP=PP=1、KV 已就绪的 Decode，BF16 计算/KV/Combine、INT8 Dispatch。每轮包括 embedding、各块的 norm/MLA/residual/FFN，以及 final norm、输出头和 sampling；MoE 中 routed 分支与 shared expert 分支共同完成后继续。旧 service/1～3 的层段语义保留。

每个 rank 只为本地批次 Token 创建本地阶段，零本地 Token 时仍可执行远端专家和状态工作。所有计算竞争本 rank 的一个槽；层间只有本地依赖，没有全 rank 屏障。参考成本必须匹配完整模型结构，改变 dtype、维度或层型不能静默沿用。

检查特别覆盖注意力、router、Dispatch 打包和 Combine 归约只计一次；没有 shared expert 时不生成共享分支；未知目标的观察结束不当作正常 EOS。所有成员图完成后才推进一次 Token。端侧通信记录仍只表示 Dispatch/Combine，新增计算阶段不伪装成通信操作。

## 可核对结果

基准两组共五轮，305 次网络写，120,316 B 应用载荷，其中 113,664 B 为业务，完成于 131,279 ns。它采用与旧层段不同的显式成本，不能把耗时差当作模型加速。

对照包括慢 shared expert、慢 sampling、1 Gbit/s 链路、三个 KV 块、不同组 Token 预算、观察结束、来源开关和场景入口。加长 shared expert 会竞争本地计算槽，不保证它一定成为最后完成的分支。

另一组 EP=3、六专家、H=1024 的合成输入执行 61 块，即前三块 dense、后 58 块 MoE；共 3,102 节点、986 次交付、754 次网络写，完成于 315,509 ns。MoE/dense/MoE 与无 shared expert 的组合为 114 节点、34 次交付、26 次网络写，10,829 ns 完成。这些是结构测试，未使用目标 R1 维度、256 专家、128 rank 或完整 CM384 网络。

计划 schema 为 `moe-ub-full-model-plan/1`。`executed_layer_ids` 含全部块，`communication_layer_ids` 只含 MoE；每 rank 的 model_stages 保存批次、上下文、成本和依赖。Chakra 与计划逐节点核对，C++ 独立重建模型/成本图，并拒绝运行中改变部署声明。

成功且至少完成一张图后，`full_model_graph_execution_validated=true`；空输入或失败仍为 false。`numerical_model_executed=false` 和 `unvalidated_reference` 保留。旧输出中的 full_model_execution_validated/target_deployment_validated 表示其声明的验证边界，不能替代后续[完整首轮验收](../m10_research_slice/README.md)，也不能证明硬件校准。

## 复现入口

从仓库根目录使用已安装环境和兼容后端，输出目录必须不存在：

```bash
PYTHONPATH=python python -m moe_ub_cosim.service \
  --input fixtures/m10_full_model/service.json --layer fixtures/m10_full_model/layers.json \
  --backend build/m10-full-model/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m3_shared_bottleneck/network \
  --output-dir runs/full-model-example --max-simulation-ns 4000000
```

[完整服务检查](../../tests/integration/check_full_model.py)和[结构检查](../../tests/integration/check_full_model_structure.py)提供自动对照，具体参数见各自 `--help`。历史命令、失败与回归结果保留在[原说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_full_model/README.md)。
