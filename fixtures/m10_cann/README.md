# CANN 通信行为的测试输入与结果

本目录验证 `cann_a3_v850_fullmesh_v1`：按每个 Token、每个专家的贡献生成 Dispatch 数据、计数发布、Combine 数据和完成标志，再由 ASTRA 与 ns-3-UB 执行依赖。它是依据固定源码编写的通信行为模型，不执行 CANN 内核或数值张量。

当前字节、缓冲、发送并发和源码对应关系集中在[模型与验证范围](../../docs/model_validation.md)。旧 `direct_rank_dedup_v1` 另保留自己的去重语义，不能用本目录的预期核对旧模板。

## 固定来源与输入

[source-evidence.json](source-evidence.json) 固定 [CANN ops-transformer](https://gitcode.com/cann/ops-transformer) v8.5.0 提交 `6ead121aded45355043b502756b6592fd7c30b14` 的选定文件、大小、SHA-256、调用配置和 LICENSE 摘要。源码曾保存在本机 `runs/v03-cann-behavior/cann-source/`，未纳入依赖或提交。独立行为模型未链接该仓库内核。

选定配置为 A3 MoeDistributeDispatchV2/MoeDistributeCombineV2 的 `fullmesh_v1`、TP=1、Dispatch dynamic INT8（quant_mode=2，无 smooth_scales）、Combine BF16（quant_mode=0）、expertShardType=0。未覆盖 fullmesh_v2、专用 shared expert rank、弹性/特殊专家、其他量化或 TP 配置。当前时序仍标记 `unvalidated_reference`。

| 文件 | 用途 |
|---|---|
| [service.json](service.json)、[layers.json](layers.json) | 两个三 rank 组、三层、七请求；H=1024、六专家、Top-2，INT8 Dispatch、BF16 计算和 Combine。容量使用明确命名的合成 GQA 模型，八个可用 KV 块。 |
| [layer.json](layer.json) | H=1025，检查非对齐向量、分块及业务字节范围。 |
| [source-evidence.json](source-evidence.json) | 源码依据和模型选择，不能作为硬件时序校准结果。 |

路由均为合成非均匀记录。各 rank 的物理输入槽数相同，用前缀 active mask 表示实际批次；零活跃 Token 不表示原生算子允许 BS=0。同 rank 保留专家计算，同芯片不同 rank 使用本地交付，跨芯片才创建 URMA_WRITE/RTP 事务。

## 预期与实际检查

基准五轮消费 36 条层路由、72 份专家贡献：同 rank 14、同芯片 21、跨芯片 37。包含 126 次零计数发布和五次多个专家位于同一目标 rank；后者仍保留每个专家的贡献。共执行 305 次网络写、123 次本地交付，业务 113,664 B、应用元数据 5,616 B、填充 1,036 B。输入、字节、依赖、资源释放和端侧记录均独立核对；观察到十处层间重叠，没有强制全 rank 层间屏障。

| 对照 | 完成时间 ns | 网络写次数 | 核对内容 |
|---|---:|---:|---|
| 两组共享 10 Gbit/s 链路 | 131,530 | 305 | 两组首轮及下一轮提交晚于各自独立运行。 |
| 链路 1 Gbit/s | 1,214,814 | 305 | 字节不变，实际排队和后续批次后移。 |
| chunk=257 | 176,178 | 638 | 业务、元数据、填充总量不变。 |
| 每 rank 一个发送槽 | 143,044 | 305 | 实际峰值 WQE 不超过一。 |
| 一个参考 lane | 144,155 | 305 | 发送顺序与并发改变执行时间。 |
| 局部参考成本乘 100 | 475,621 | 305 | 后续依赖随局部计算变化。 |
| 全部同芯片 | 13,330 | 0 | 354 次本地交付，专家贡献保留。 |

13 次服务运行覆盖独立组、共享组、空输入、场景 YAML、来源开/关及无观测。来源开关不改变结果和原生网络日志；1 µs/10 µs 重新采样保持业务归属总量。

H=1025 的两个专家贡献在 chunk=1024/17 下分别产生 20/382 次写，均为 6,150 B 业务、352 B 元数据、118 B 填充。包内业务归属不包含 scale、三元组、状态或填充。三项前端非法配置、五项后端计划篡改均在业务提交前拒绝。

## 复现入口

从仓库根目录使用已安装的 Python 环境和兼容后端构建，输出目录必须不存在：

```bash
PYTHONPATH=python python tests/integration/check_cann_behavior.py \
  --build-dir build/m10-cann-behavior --output-dir runs/cann-check
```

脚本生成并保留各对照输入、命令和结果。历史最终检查位于 `runs/v03-cann-behavior/cann-final/summary.json` 与 `commands.json`；完整命令、失败及回归记录可查[整理前说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_cann/README.md)。这组小场景验证行为与依赖，完整目标另见[首轮验收](../m10_research_slice/README.md)，两者均不代表 NPU/CANN 时序已校准。
