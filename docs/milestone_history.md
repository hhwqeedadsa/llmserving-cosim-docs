# 开发里程碑历史

本页只索引已完成阶段和证据。日常操作见[用户指南](user_guide.md)，现行能力与模型限制见[系统架构](architecture.md)及[模型依据](model_validation.md)。表中的完成均限于对应记录声明的输入和检查范围。

| 阶段 | 交付内容 | 证据入口 |
|---|---|---|
| M0：原生基线与联合构建 | 固定上游版本、复现原生样例，检查 ASTRA/Chakra 与一套 UB/ns-3 的联合链接 | [联合链接检查](../scripts/m0_joint_build/README.md)、[原始环境记录](archive/README.md#历史记录) |
| M1：计算与一对收发 | 使用 ns-3 时间执行计算，分开接收就绪与发送完成，记录端口实际包及来源 | [最小传输](../fixtures/m1_transfer/README.md) |
| M2：MoE 层与连续图 | 单层任务生成、分块与发送槽、延迟反馈，以及连续图的身份和回收 | [单层](../fixtures/m2_single_layer/README.md)、[延迟反馈](../fixtures/m2_delay_feedback/README.md)、[变体](../fixtures/m2_variants/README.md)、[连续图](../fixtures/m2_sequence/README.md) |
| M3：动态服务与共享网络 | 请求到达、按完成推进服务、独立组争用链路和正常结束 | [服务](../fixtures/m3_service/README.md)、[到达](../fixtures/m3_dynamic_arrivals/README.md)、[瓶颈](../fixtures/m3_shared_bottleneck/README.md)、[边界](../fixtures/m3_service_boundaries/README.md) |
| M4：P/D 与数据输入 | KV 交接、容量等待、第三组竞争，以及模型和参考计算输入 | [交接](../fixtures/m4_pd_handoff/README.md)、[队列](../fixtures/m4_pd_queue/README.md)、[数据输入](../fixtures/m4_data_inputs/README.md) |
| M5：观测、多层与模型核对 | 分离观测和评价数据，执行多层路由，核对 DeepEP 参考路径的业务语义 | [观测](../fixtures/m5_observation/README.md)、[多层](../fixtures/m5_multilayer/README.md)、[DeepEP 核对](../fixtures/m5_model_audit/README.md) |
| M6：模型、部署和后端参数化 | 参数进入前后端实际执行，处理同芯片本地性，保存有效网络参数 | [执行参数](../fixtures/m6_execution_parameters/README.md)、[容量](../fixtures/m6_service_model/README.md)、[本地性](../fixtures/m6_locality/README.md)、[网络适配](../fixtures/m6_scenario_backend/README.md) |
| M7：同 rank 多请求组批 | 每请求按自己的解码位置推进，完成后补入请求，KV 不足时等待释放 | [连续组批](../fixtures/m7_continuous_batching/README.md) |
| M8：多请求、多层、多组组合 | 独立组在同一网络中推进完整层段，通信完成变化反馈到后续批次 | [组合服务](../fixtures/m8_composed_service/README.md) |
| M9：真实路由与计算依据 | 固定真实 R1-AWQ 路由来源、区分观察结束与请求完成，接入 A100 小型算子测量 | [来源与计算检查](../fixtures/m9_evidence/README.md) |
| M10：CANN 与目标首轮 | CANN 行为、完整模型和拓扑、1024 请求来源、资源改进，以及可复用首轮、采样和后端重放 | [CANN](../fixtures/m10_cann/README.md)、[完整模型](../fixtures/m10_full_model/README.md)、[拓扑](../fixtures/m10_fabric/README.md)、[路由](../fixtures/m10_route_selection/README.md)、[资源记录](../fixtures/m10_resource_efficiency/README.md)、[最终验收](../fixtures/m10_research_slice/README.md) |

M0 至 M5 对应 v0.2，M6 至 M10 对应 v0.3。原 F01 至 F04 分别由 M9 路由导入、M9 A100 小形状测量、M7/M8 连续组批和 M10 CANN 行为交付承接。源码核对和参考参数的完成不等于目标 NPU 性能校准。

## M10 收尾范围

2026-10-05 用户将 M10 最终验收调整为完整目标首轮的保存、离线重采样与后端重放。该范围已完成，统一命令及隔离用户流程也已通过；完整 64 步服务不再作为本版收尾条件，且未被记录为通过。详细结果和资源开销只在[最终验收记录](../fixtures/m10_research_slice/README.md)维护。

同日，主仓库和有修改的依赖按由内向外顺序发布，文档整理的基线为 `e20d42a2bcf0b2ac2529b8641a9492638ffc786f`。历史的失败、预算停止和中途“尚未完成”保留在对应证据及[原始记录](archive/README.md)，不表示当前仍处于那些阶段。
