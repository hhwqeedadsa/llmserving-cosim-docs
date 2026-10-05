# 多层、多解码步骤的连续数据

本样例使用锁定 Mixtral-8x7B-v0.1 的结构和完整容量核对，执行第 6、7、8 层的连续片段。四个给定 KV 已就绪的请求各推进三个解码步骤，路由来自明确标注的合成文件，各层计算使用显式参考值。没有真实专家轨迹或目标 NPU profile，结果只证明功能数据管线与依赖执行一致。

## 已验证入口

按 [M5.2 环境记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/environment.md#2026-09-29m5-2-multilayer-continuous) 配置并构建 `build/m5-multilayer`，从仓库根目录运行，输出目录须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research run \
  --config fixtures/m5_multilayer/run.json \
  --backend build/m5-multilayer/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-multilayer
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research validate-run \
  --run-dir runs/my-multilayer
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.research validate-observed \
  --observed-dir runs/my-multilayer/observed
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_multilayer.py \
  --build-dir build/m5-multilayer --output-dir runs/my-multilayer-check
```

`run.json` 沿用 M5.1 研究入口与本地时钟配置，观测交换机 4 的端口 0、2。`service.json` 使用新增 `moe-ub-multilayer-service/1`，保留 M4.3 的模型文件摘要、dtype、容量、请求和路由来源字段，增加 `layer_profiles`。其中每项含 layer_id 与 compute_profile_ns，必须按 2～32 个连续层排列，首层等于 layer.json 的 layer_id，层号位于实际模型的 0～31。

当前多层入口限给定 KV 的解码、一个四成员 EP 组、每活跃 rank 每步一个真实 Token，固定专家放置为每 rank 两个专家；请求仍可分别到达、具有不同初始位置或剩余步数。compute 必须为 fixed_reference，四个实际阶段耗时为正，next_layer 必须为零。原单层和 P/D schema 保留原行为，本步没有将多层计算加入 P/D。

## 连续路由与计算

routes.jsonl 含 36 条记录：四个请求 × 三步 × 三层，source_index 为 4000～4035。每条保存 request_id、decode phase、绝对输入位置 source_token_index、layer_id 和专家选择；导入时用 `source_token_index - prompt_tokens` 转为解码索引，并保留源文件摘要、行号和 source_index。四个请求的解码起点分别为 1、2、0、1，不以批次步骤代替请求位置。全部声明记录恰好消费一次，缺失、重复、错层或错位置均失败。

合成文件采用三个手工表；各步按层轮换这些表，文件已显式列出每个请求/步骤/层，没有在执行时复制首层路由：

| 表 | rank 0 / 1 / 2 / 3 的专家选择 | 每层真实写 |
|---|---|---:|
| 远端参考 | [2,4] / [0,5] / [4,6] / [1,3] | 14 |
| 同目的 rank 去重 | [4,5] / [0,5] / [4,6] / [1,3] | 12 |
| 全部本地 | [0,1] / [2,3] / [4,5] / [6,7] | 0 |

第 0/1/2 步分别按三层使用 A-B-C、B-C-A、C-A-B。每步 26 次写、212992 业务字节，三步共 78 次、638976 字节。本地表没有网络写，但保留专家和合并计算。逐层 attention_route_pack、expert_per_input 等参考值在 service.json 中明确列出；没有外推 M4.3 的历史 H100 算子数据，也没有添加任何固定网络等待。

## 图、完成条件与来源

每个解码步仍只提交一个图集合，各 rank 的一份 Chakra 图包含全部选定层。Python 先独立生成并校验各层，再合并局部节点号；每层根节点依赖本 rank 前层的末节点，空层保留此前依赖直到下一个有工作的层。下一层在本地条件满足后即可执行，不等待远端上一层的最晚完成。原单层 next_layer 参考节点被省略，由真实下一层阶段接续；不会重复计时。

`moe-ub-multilayer-plan/1` 的 layers 保存逐层计划与源记录，rank_graphs 保存唯一实际执行的合并图。分层计划是校验资料，不单独提交或执行。C++ 复用单层语义检查，独立重建层间映射，逐字段比较合并结果和全部 Chakra 文件，全部通过后才装载、激活各 rank。传输、UB 任务、comm_tag 在整个运行内唯一；层与步骤具有独立操作身份。原 ASTRA 执行和 UB 回调机制不变。

组内所有 rank 完成整张多层图后，原 Scheduler.add_done 才完成一次 Token，下一步按此次完成事件的 ns-3-UB 时刻组批。历史里的服务 tokens 保留调度器绝对位置；各层计划 tokens 使用规范化解码索引，源记录保留二者映射。计划顶层 input 是共享配置，层号、Token 和计算数据位于 layers[*].input。传输来源的 source_record_index 指向所属层的 input_provenance.route_records，并结合 layer_id 定位，不把它当成跨层平铺列表下标。

端侧记录取自各 rank 各层的 ASTRA 阶段开始/完成事件；空阶段使用本地前驱的实际完成时刻。内部操作注册表绑定对应的本地编号，正式 observed 仍只有 M5.1 的五个端侧字段、端口窗口和白名单配置，不新增层、图或来源答案。唯一业务交付、每窗整包分类与操作归属分别检查，访问边界见 [M5.1 说明](../m5_observation/README.md)。

## 实测与边界

主样例在 193789 ns 完整结束，三次 Token 完成时刻为 64310、128538、193789 ns。生成 18 个内部操作、72 条端侧记录和 2004 个窗口（1961 个零窗口），36 条路由全部消费，请求、KV、图、任务及网络全部收尾。首轮某 rank 在 16675 ns 已进入第 7 层，而所有 rank 的第 6 层要到 20552 ns 才结束，证明没有附加层间全组屏障。

| 检查输入 | 层数 / 实际组批轮数 | 写次数 | 唯一业务字节 | 完成（ns） |
|---|---:|---:|---:|---:|
| 主样例、重复、来源关闭、零时钟 | 3 / 3 | 78 | 638976 | 193789 |
| 仅 rank 1 链路传播由 20 改为 2020 ns | 3 / 3 | 78 | 638976 | 256536 |
| 两请求，第二请求在 1000 ns 到达 | 3 / 4 | 42 | 344064 | 216871 |
| 仅 chunk_bytes 改为 3072 | 3 / 3 | 234 | 638976 | 199717 |
| 显式 32 层、每请求两步 | 32 / 2 | 558 | 4571136 | 1367802 |

八次正常研究运行、一次无观测对照、十项预期失败以及相关回归均通过。来源开关、重复和零时钟保留相同图、服务/协议、实际 ASTRA/UB 结果与原生网络日志，来源开关的观测文件也相同。网络延迟变化会实际推迟依赖计算和下一步提交。非法依赖、跨层重号、层序或末 rank 图在 0 ns 被原子拒绝；仿真上限保留未完成传输并报错。

32 层用例显式展开 256 条合成路由，验证全部 MoE 块的连续依赖和通信；仍未覆盖 embedding、输出头等完整模型算子图，也没有真实数值前向和目标计算数据。因此 manifest 保持 `full_model_execution_validated=false`、`latency_scope=multilayer_segment_iterations_not_full_model_ITL_or_TTFT`、`unvalidated_reference`。本用例验证所选层段的依赖与数据记录；目标通信源码核对见[DeepEP 参考检查](../m5_model_audit/README.md)，当前完整模型范围见[模型与验证范围](../../docs/model_validation.md)。
