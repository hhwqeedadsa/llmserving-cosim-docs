# 部署本地性样例

本样例使用显式芯片归属区分同 rank、同芯片不同 rank、跨芯片三类贡献。所有路由和计算时间都是人工参考输入，状态仍为 `unvalidated_reference`；没有模拟 die 间链路、采集真实路由或校准硬件。

## 输入格式与约束

`moe-ub-layer-fixture/2` 在原单层字段之外要求 `deployment`：

| 字段 | 含义与检查 |
|---|---|
| `network_scope` | 本步只接受 `cross_chip`。 |
| `ranks` | 与 `rank_ids` 一一对应且顺序相同；每项包含 `rank_id/die_id/chip_id/server_id`。 |
| `die_id` | 运行内唯一的非空字符串，一个 die 对应一个独立计算 rank。 |
| `chip_id` | 全局芯片身份；多个 die 可以引用同一芯片，但该芯片只能属于一个服务器。样例的 `server0.chip0` 与 `server1.chip0` 是不同芯片。 |
| `server_id` | 非空的服务器身份。 |
| `local_prepare_ns_per_vector` | `dispatch/combine` 两个非负整数，表示每份同芯片跨 rank 向量的源端准备时间，单位 ns。零表示显式零参考成本，不创建零时长计算节点。 |

逻辑 rank、物理 die/chip/server 与 `rank_to_ub_node` 分别记录。后者连同实际网络目录的 `node.csv/topology.csv` 给出 fabric 接入关系；映射仍须单射并指向已有 UB 计算节点。同芯片的两个 rank 既不合并计算槽，也不共享或重复计算容量预算。本输入仅声明归属，不生成 CM384 网络拓扑。

旧 `moe-ub-layer-fixture/1` 不接受 deployment，全部跨 rank 贡献仍走 UB。迁移必须显式改为 v2 并填写完整归属，不能只按相邻 rank 或 UB 节点号推断芯片。

## 执行及记录

先按 Token 的目标 rank 去重，再分类。不同专家位于同一目标 rank 时只交付一份激活向量，并在该 rank 合并为一份回传贡献；专家计算仍计每个 Token/专家配对。

- 同 rank：沿原计算图推进，不创建 SEND/RECV 或 UB 任务。
- 同芯片不同 rank：每阶段先在源 rank 执行一次本地准备 COMP，耗时为该阶段本地交付向量数乘相应参考时间。该节点占用源 rank 的唯一计算槽；其后 SEND 发布数据，RECV 在源数据就绪且自身已登记后完成。两侧回调都通过 ns-3 当前时刻的事件排队且只执行一次。本地 SEND 使用现有 ASTRA 发送槽限制，回调后释放；没有 WQE、Jetty、fabric 包或 UB 事务完成事件。
- 跨芯片：保留原分块及 WRITE/RTP；目标写提交表示数据就绪，源事务完成释放发送槽。

专家计算等候本 rank 必要的 Dispatch 交付，最终合并等候必要的 Combine 贡献。没有增加全组阶段屏障。不同 die/rank 保留各自的计算槽，准备计算和其他计算不能在同一 rank 重叠。

新 `moe-ub-execution-plan/2` 的 `contributions` 保存三类逐 Token/目标 rank 贡献及专家编号。`transfers` 是跨 rank **逻辑交付**列表：`urma-write-rtp-default` 才是 UB 写；`same-chip-local-reference` 是本地交付，省略 `ub_task_id`，`priority/task_byte_range` 为 null，不按 fabric 的 chunk_bytes 分块。transfer_id 和 comm_tag 在全部交付中唯一，UB task 分配器只为 fabric 写分配标识。`rank_graphs.local_prepare_nodes` 记录两阶段准备节点或 null。Chakra 序列化沿用原 COMP/SEND/RECV 格式，文件元数据版本保持原值，具体交付语义由 v2 计划校验并登记。

后端结果的 `transfers` 继续只记录ns-3-UB 写，新增 `local_deliveries` 单独记录本地发布、接收登记和两侧回调时间。旧 `sender_completion=transaction_done` 字段只适用于 fabric WRITE；本地发送回调表示数据已发布，不表示 UB 事务完成。端侧阶段时间包含实际本地工作及等待，端口计数和 `truth/transfer_registry.jsonl` 只包含实际 fabric 事务；本地身份不会加入 observed 字段。

`moe-ub-configured-service/1` 可配合 layer v2 使用，唯一 group 必须重复声明完全一致的 deployment。生成的后端部署文件使用 `moe-ub-service-deployment/2`；每轮都校验部署未改变。每个 rank 仍按原独立预算核算权重与 KV，执行一组、一层、每 rank 同时一个请求。本步也覆盖固定两轮；旧服务、P/D、来源索引及多层入口明确保留 layer v1，不自动迁移。多请求连续批处理和多组层段的对应输入分别见[M7](../m7_continuous_batching/README.md)与[M8](../m8_composed_service/README.md)。

## 手算对照与实际结果

`mixed-layer.json` 使用 rank 2/5/9/12，分别映射 UB 节点 2/0/3/1；2 与 5 同芯片，9 与 12 同芯片。四个 Token 共 12 份专家输入，逐 rank 为 2/5/3/2；去重后 4 份同 rank、2 份同芯片跨 rank、5 份跨芯片贡献。每向量 128 字节，两阶段合计 4 次本地交付、512 逻辑字节，以及 10 次 UB 写、1280 fabric 业务字节。实际完成于 1376 ns。

`local-layer.json` 只有 rank 2 的一个 Token，目标两个专家都在 rank 5。它产生两次本地交付，无 UB 写、目标写提交或端口业务字节。完成时间为 `100 + 70 + 2×200 + 30 + 110 + 40 = 750 ns`；Dispatch 准备增加 1000 ns 后完成于 1750 ns。显式零本地准备成本时为 570 ns；固定两轮为 1500 ns。

`service.json/service-layer.json` 沿用上一项三 rank GQA 模型，把 rank 2/7 声明为同芯片。权重仍为 255872/280448/231296 字节，KV 容量仍为 3/3/2 块。六轮产生 18 次本地交付、6 次 UB 写和 1152 fabric 业务字节，完成于 103158 ns；增加本地准备成本会推迟下一轮提交，最终为 133080 ns。只保留第一个请求时，四轮八次本地交付正常结束，网络发送/到达包计数均为零。

这些值是当前合成参考模型的功能结果。服务中的本地准备计算会占用源 rank 的计算资源，不能据网络字节减少就推断服务必然更快。

## 已验证命令

使用[历史环境记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/environment.md#2026-10-03m6-部署本地性分类)中的独立构建，以下命令从仓库根目录执行，输出目录必须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.generate \
  --input fixtures/m6_locality/mixed-layer.json --output-dir runs/my-locality-plan
build/m6-deployment-locality/output/cosim/ns3.44-cosim-layer \
  runs/my-locality-plan fixtures/m1_compute/system.json \
  fixtures/m2_single_layer/network fixtures/m2_single_layer/observation.json runs/my-locality-layer

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m6_locality/service.json --layer fixtures/m6_locality/service-layer.json \
  --backend build/m6-deployment-locality/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json --network fixtures/m2_single_layer/network \
  --output-dir runs/my-locality-service

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_deployment_locality.py \
  --build-dir build/m6-deployment-locality --output-dir runs/my-locality-check
```
