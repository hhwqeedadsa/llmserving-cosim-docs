# 用逐请求专家选择驱动Scale-Out网络

本例把四个**人工构造**的路由文件交给真实LLMServingSim调度器，生成Chakra图，经ASTRA和单一非UB原生ns-3执行两轮Decode。它验证数据接口、通信字节和完成反馈，不是Hugging Face原始样本，也不是热点优化效果实验。四卡部署在两台服务器、两个Pod，每卡一个请求，EP4；执行原模型层1—2的有限片段。模型与计算成本都是声明输入。

## 运行

从仓库根目录使用已有前端依赖的Python、已经构建的非UB后端和protoc，设置`PYTHONPATH=python`。下列输出目录必须尚不存在；先建立其父目录。后端路径从`rdma-build`生成的`backend.json`读取，构建方法见[RDMA说明](../scaleout_rdma/README.md)。输出卷须保留8 GiB；单例维持60秒、RSS1 GiB、地址空间2 GiB、产物512 MiB、模拟时间5 ms限额。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_route_replay/topology.json --output /path/to/new/topology

python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_route_replay/service.json \
  --topology /path/to/new/topology \
  --protocol examples/scaleout_route_replay/rdma.json \
  --backend /path/from/backend-json/service --protoc /path/to/protoc \
  --output /path/to/new/baseline

python -m moe_ub_cosim scaleout rdma-check --run /path/to/new/baseline
```

将`--config`改为`service-permuted.json`，另选新输出目录，可保持专家选择不变，只改变专家放置。两种输入每层每卡仍各有一个专家，不增加显存容量或免费副本；不保证人工重排改善网络时间。

## 输入怎样对应

`manifest.json`声明来源、模型、原始层结构和每个文件的SHA-256。单文件外层数组的入口0为Prefill，后续入口为Decode；每层值是token×Top-K，Dense层为null。原始四层结构完整保留，执行时只选层1—2。

`service.json`的`route_replay`字段：

| 字段 | 含义 |
|---|---|
| `manifest.path / sha256` | 原始清单路径和内容摘要；开始运行时相对仓库根目录解析 |
| `layer_ids` | 明确的原始连续MoE层号，数量等于`model.layers` |
| `requests` | 每个rank、每个slot对应唯一`file_id`及`first_decode_entry`，后者从1开始 |
| `request_layout` | 可选，默认`dense_per_rank/1`要求填满每卡slot；`sparse_explicit/1`只执行明确列出的请求，保留无本地请求rank的专家工作，每个EP组至少一个请求 |
| `budget` | 本轮最多读取的文件数和累计输入字节；无自动抽样或循环补齐 |
| `expert_to_group_rank` | 可选，逐层列出每个专家由EP组内哪个成员持有；默认连续放置，显式放置须保持每卡专家数相同 |

源请求实际context为`prompt长度 + first_decode_entry − 1`，本例是17、34、20、46，`context_tokens=64`仅为上限。每轮取每个请求自己的下一个入口；下一轮由上轮完成回传触发。源窗口不够直接拒绝，不循环、不推断EOS。观察结束标记为`OBSERVATION_ENDED`，调度器内部有限长度状态另存`scheduler_state`。

`routes/`保存所用原件的逐字节副本及归一化路径清单；`input.json`引用运行内清单，因此复制整个运行目录后仍能独立校验。`graphs/*/wave.json`同时记录原层号、文件ID、入口、token行和原件hash。`rdma-check`重新读取快照核对专家选择，再独立重算GPU对通信字节及网络完成因果。

## 换成已授权的真实数据

1. 先按[数据集研究说明](../../docs/reviews/moe_trace_dataset_hotspots_20261018.md)建立真实来源manifest，保留原始模型名、层结构、E/K、文件路径和SHA-256。格式沿用`moe-ub-route-manifest/1`，来源`kind`使用真实声明，不把人工数据改标签冒充原件；原始受限数据留在忽略的`data/`或运行目录，不提交Git。用户提供、尚未核实远端版本的文件使用`user_provided_model_routes`，`dataset_revision: null`且`unknown_fields`必须包含`dataset_revision`；这只声明本地文件身份，不证明远端版本/采集环境。若版本已知，填写40—64位十六进制提交并移除该未知项；`published_model_trace/captured_model_trace`仍要求固定版本。每个原件的结构与SHA-256继续核验，快照保留完整来源声明。
2. 将配置中的模型名、hidden、E/K和层号与来源匹配。R1只能先用`service/1`做1—4个连续MoE层的网络片段，计算与KV仍为通用声明。`service/2`要求来源所有层均被执行，目前是全MoE GQA结构，不能执行含Dense与MLA的完整R1。
3. 默认给每个rank的每个slot分配不同的原始请求文件，并明确起始Decode入口。独立原件不足时可显式选择`sparse_explicit/1`，只列出实际请求；未列出的slot不会生成请求，batch是每卡上限。增大实际并发需要更多独立文件，不能拿同一请求的连续token冒充并发请求；当前batch是每卡1—16，回放步数1—128且须满足其他预算。128/256/512卡Clos的准备和限制见[稀疏请求示例](../scaleout_sparse_clos/README.md)。
4. 明确激活字节、专家放置、网络和声明计算成本；权重AWQ/FP8名称不能确定激活dtype。本入口回放选择，不还原原集群的到达时间和源卡位置，仍从ready-KV Decode开始，不执行Prefill。

通信采用逐专家贡献复制：每个贡献为`hidden_size×activation_bytes`，同卡不发网络，同机走机内织构，跨机走RDMA；按GPU对合成消息，Combine反向返回同量向量。不实现DeepEP的跨专家目的地去重、复制专家或动态迁移。

建议先固定请求和专家选择，对比连续放置、等容量重排、不同跨Pod位置和逐层带宽；同时报告机内/跨机字节、NIC/交换机排队及Decode完成。原论文的Prefill预测与计算均衡应作为已有基线；真实数据尚未取得时不报告真实热点排名或收益。
