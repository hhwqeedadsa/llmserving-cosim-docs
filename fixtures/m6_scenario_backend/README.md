# M6 场景与 WRITE/RTP 参数入口

`reference.yaml` 引用已有小型 GQA 服务、一个选定 MoE 层和四端点交换网络，从 CM384/R1 参考规格中只导入 `ns3_backend.reference_attributes/reference_globals` 及按角色选择的事务 profile。它不执行参考规格中的模型、384 芯片拓扑或 CANN 流程；原 `moe-ub-scenario-reference/0.1` 文件仍不能直接作为可执行输入。

入口沿用同一配置服务、ASTRA 图和 UB 网络。本目录旧 schema 样例的执行边界是一组、一个选定层、每 rank 同时一个请求；路由和计算耗时来自人工参考输入，状态为 `unvalidated_reference`。M7 已另行验证引用 service/2 与 layer/3 的[连续批处理输入](../m7_continuous_batching/README.md)，M8 又验证 service/3 与 layer-segments/1 的[多组层段输入](../m8_composed_service/README.md)，不改变本目录样例的原行为。

## 已验证命令

依赖为现有 `.venv/llmservingsim-native` 中的 PyYAML 6.0.1；无需下载模型。独立构建命令见[环境记录](../../docs/environment.md#2026-10-03m6-场景-yaml-与后端参数适配)。以下命令从主仓库根目录执行，输出目录必须不存在。

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario resolve \
  --input fixtures/m6_scenario_backend/reference.yaml \
  --backend build/m6-scenario-backend/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-scenario-resolution

PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario run \
  --input fixtures/m6_scenario_backend/reference.yaml \
  --backend build/m6-scenario-backend/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-scenario-run

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_scenario_backend.py \
  --build-dir build/m6-scenario-backend --output-dir runs/my-scenario-check
```

`resolve` 调用实际后端导出目录并校验参数，生成输入后停止，不表示已经仿真通过。`run` 先解析，再调用现有服务入口，读取实际对象快照与执行结果。观测配置由既有后端在业务启动前校验；执行失败保留日志和失败状态。

## 输入与覆盖顺序

本目录入口要求 `schema_version: moe-ub-scenario-run/1`，未知字段、重复 YAML 键和不兼容配置均报错。所有文件路径相对于场景 YAML 所在目录解析。

| 字段 | 含义 |
|---|---|
| `scenario_id/basis` | 运行身份；当前依据只能为 `selected_reference`。 |
| `service/layer/system` | configured-service/1 配 layer/1、layer/2，configured-service/2 配 layer/3，configured-service/3 或 evidence-service/1 配 layer-segments/1，以及 ASTRA system JSON；模型、专家放置、容量和映射仍按对应合同校验。 |
| `backend.reference_file` | 原参考规格。只读取上述后端段，所选 profile 必须完整；未选候选允许缺项。 |
| `backend.profile_by_role` | 显式选择 `dispatch/combine`，当前均要求 URMA WRITE/RTP、数据拥有方发起、RemoteWriteCommitted 就绪、事务完成释放发送槽。 |
| `backend.attributes/globals` | 原生完整键名和标量值；未知名称、类型/单位错误或矛盾的固定约束报错。带单位时间使用 `10ns` 等字符串，带宽属性使用 `224Gbps` 等原生格式；生效值按原生序列化形式保存。 |
| `backend.tp_max_data_payload_bytes` | 必须为 4096，来自锁定源码的 `UB_MTU_BYTE`，不是运行时可调参数。 |
| `backend.transport_channel_setup` | 必须为 `precreated`；这是当前联合入口的明确选择；不代表原生 UB 不能按需创建通道。 |
| `network.case_path/plane_id` | 显式 CSV 连通关系、路由与通道，以及单平面的记录身份。不会根据模型自动推断或构造目标拓扑。 |
| `network.defaults` | `rate_bps/delay_ns/allocation_ns/forward_ns`，均为带字段单位的整数；带宽大于零，时间非负。 |
| `network.nodes/links` | 显式覆盖列表；每个节点项填写 `node_id/allocation_ns/forward_ns`，每个链路项填写 `src/src_port/dst/dst_port/rate_bps/delay_ns`。不允许重复或未知节点、链路；反向书写同一链路等价。 |
| `execution` | `chunk_bytes/send_slots_per_rank/seed/run`；发送槽为 1 或 2，chunk 同时进入图与容量校验，RNG 进入实际后端。 |
| `observation/max_simulation_ns` | 原端口观测对象（也可为端口集合）或显式 null，以及最大仿真时间。 |

属性覆盖依次为运行时注册的源码默认值、所引用的参考属性、场景属性、联合驱动固定约束。显式选择与固定约束冲突会拒绝，不静默改写。固定约束包括 WRITE/RTP、CBFC/SP/HASH、关闭多路径与喷洒、CC、故障及重传，业务优先级仍为 7，priority/VL 数为 16。驱动关闭原生队列定时采样及自动解析，观测使用现有被动接口。

节点/链路另有更高优先级的 CSV 实例覆盖：读取原案例的连通关系，使用 `network.defaults` 替换其全部速率和时间，再应用 `nodes/links`。不会继承原案例的 `network_attribute.txt`，也不会把原 CSV 中的时间当作最终生效值。节点连续编号、全部声明端口连接、无环单路径及映射端点对的双向预建通道在解析时核对。本目录 `/1` 入口不验证多平面、多候选路由/喷洒、READ、CTP 或 native LD/ST。M10 已新增独立 [scenario-run/2 fabric 输入](../m10_fabric/README.md)，在参数化二级网络中验证多平面和 HASH 最短候选；没有放宽 `/1` 的 CSV 约束。

多组层段输入要求预建通道覆盖每组内的端点对，独立组之间不产生业务通信，因此不要求组间端点两两建通道。`resolution.json` 的 `transport_channel_required_groups` 保存这一检查范围；各组仍共享同一物理拓扑及队列。execution 的 chunk/发送槽应用到所有层并重新检查容量和完整层段。

## 生效记录

后端的 `--backend-catalog` 从运行时 TypeId 和 GlobalValue 枚举，当前构建共 96 个 UB 属性、21 个 UB 全局值；包含延迟注册的原生工具全局值和出口队列类型，不另维护默认值表。目录记录 UB 提交和编译常量；`--check-backend FILE` 再用原生 checker 校验并规范化。目录不匹配当前构建时拒绝执行。CMake 在配置时记录 UB 提交，依赖变更后应重新配置；当前任务没有变更依赖。

- `requested_backend.json` 保留选择值、运行时默认值和逐参数来源链；`network/backend.json` 保存规范化结果，是新入口的权威输入。完整 `network_attribute.txt` 是审计副本，新路径不再从它加载参数。旧案例未提供 backend.json 时，仍走原 TXT 路径。
- `resolution.json` 保存导入范围、输入摘要、固定依赖、二进制及适配器摘要、网络覆盖、三个分包口径、RNG/观测输入和完成状态。运行后补记实际执行目录中四份 CSV 的摘要。
- `run/execution/effective_backend.json` 读取实际 switch、allocator、routing、queue、port、application、connection manager、TP 的属性，包括继承属性；读取实际链路速率/延迟和节点时间，核对所选值与实例覆盖一致。还核对实际全局值。Jetty 动态创建，其 TA 和 inflight 值在每个 WQE 启动回调时核对并写入 `result.json`。
- 全量目录中存在 CTP、CC、native LD/ST 等未启用属性，只表示其默认/配置值可追溯，不表示这些模式执行过或已校准。实际对象列表、激活 profile 和任务结果分别记录；同芯片本地交付沿用独立本地回调，不计为 WRITE。

新 backend.json 路径的原生 PortTrace 在 `run/execution/runlog`；沿用 TXT 的旧服务在 `execution/network/runlog`。两者的队列检查都对照实际原生发送记录，位置差异不表示缺少 trace。

集合 chunk 决定 UB 任务数，TA segment 决定每个任务的远端写提交分段，TP 最大数据载荷决定包级切分。三者不能互换。队列容量、信用值和 inflight 上限仍会影响执行；不保证任意合法数值都足以完成某个负载，耗尽、丢包或超时按既有失败检查报告，不降级为固定通信时间。

## 实际小场景结果

基准为 224 Gbit/s、每链路 20 ns、节点仲裁 1 ns、chunk 1024、TA 65536、TP 4096。虽然属性默认仲裁为 10 ns，实际节点使用 CSV 的 1 ns。

| 对照 | 完成时间 ns | UB 写 / 目标提交段 | fabric 业务字节 |
|---|---:|---:|---:|
| 基准 | 103188 | 6 / 6 | 1152 |
| 速率 1 Gbit/s | 118290 | 6 / 6 | 1152 |
| 链路延迟 2000 ns | 126948 | 6 / 6 | 1152 |
| TA 64 字节 | 103191 | 6 / 18 | 1152 |
| chunk 64 字节、2 槽 | 103191 | 18 / 18 | 1152 |
| chunk 64 字节、1 槽 | 103488 | 18 / 18 | 1152 |
| 只保留同芯片请求 | 60720 | 0 / 0 | 0 |

基准六轮仍有 18 次同芯片交付，纯本地对照四轮有 8 次交付且发送包计数为零。带宽和延迟扰动从真实写提交传递到专家计算与下一轮调度；业务字节保持不变。来源开启、关闭及不挂观测三种运行的服务/后端结果一致。另用隐藏维度 4096 的合成模型及显式容量预算验证 8192 字节 chunk：9 次写交付 73728 字节，TA 提交为 9 段，观测到的数据包载荷均为 4096 字节。它是分包功能测试，不是模型或硬件校准。
