# 研究实验索引

这里保存联合仿真平台上的研究问题、可复现配置、分析程序和结果。历史 Scale-Up 实验与新增非 UB Scale-Out 探针分别保留适用范围。平台功能与实验分开验收，实际进度统一见[当前任务](../docs/tasks/current.md)。

## 先选择正确的实验

| 目录 | 研究内容与模型背景 | 阅读方式 |
|---|---|---|
| [mapping_value](mapping_value/README.md) | 早期 v0.4 条件下，完全正确的端网关系能否帮助排查 | 历史实验，先看其结论和适用条件，不套用为当前 A3 结果 |
| [scaleup_observability](scaleup_observability/README.md) | 早期发送槽、LOAD 响应及观测对照 | 历史实验，部分现象依赖旧资源和通信模型，不能直接当作真实硬件痛点 |
| [scaleup_issue_screening](scaleup_issue_screening/README.md) | A3 修正后的现象筛选，三类网络机制的诊断与采集成本 | 已完成的研究与结论，保留适用范围 |
| [scaleout_backend_review](scaleout_backend_review/README.md) | ASTRA/ns-3/Alibaba 接口、时间和 RDMA 完成边界 | 非UB源码与实际探针，不代表在线后端已接入 |
| [scaleout_scaling](scaleout_scaling/README.md) | 千卡输入核算、交换机固定内存及热点研究设计 | 实际分配探针与估算；尚无千卡执行或真实训练热点统计 |
| [scaleout_topology](scaleout_topology/README.md) | 两/三层Clos、Agg、multi-rail与HPN参考双平面的物理拓扑 | 公开CLI静态生成、连通性及确定性验收；未执行网络 |
| [scaleout_e2e](scaleout_e2e/README.md) | 小规模LLMServingSim→ASTRA→非UB ns-3多层多轮与网络对照 | 30次主对照、共享网络/协议验收及7组可视化；声明成本与合成路由 |
| [scaleout_faults](scaleout_faults/README.md) | 数据/ACK丢弃到协议、图节点与服务的传播 | 4组实际服务对照，保留恢复与5ms内失败，非生产恢复校准 |
| [scaleout_admission](scaleout_admission/README.md) | 软件准入、发送资源与队列/业务指标分离 | 7组固定输入对照，保留无收益和ACK占用额度的故障传播 |
| [route_alignment](route_alignment/README.md) | 保持每请求热度/通信量的跨请求时间对齐筛选 | EP8单层首条件完成；原检查失败后停止其余31条件，修正后仅离线核验，未测得对齐收益 |
| [rc_completion_reachability](rc_completion_reachability/README.md) | 请求重试状态与原子返回问题的MoE适用性筛选 | 固定v6.12部分原函数的五种前缀；未证明完整栈/当前版本漏洞，暂不作为MoE跨机主线 |
| [input_graph_baseline](input_graph_baseline/README.md) | 从真实输入直接生成普通依赖图的强基线与通用压缩 | EP4/8/16六组静态可达关系保持；依赖边压缩归入普通基线，没有网络或全流程加速证据 |

Scale-Up 的[方案](scaleup_issue_screening/plan.md)覆盖本地竞争与服务不足、下游阻塞与排队传播、多平面推进差异。新增 Scale-Out 的[研究报告](../docs/reviews/scaleout_scaling_hotspots_20261015.md)关注跨机 All-to-All-v、多 EP 组和热点到网络资源的映射；不将旧 UB 结果改称 RDMA 结果。目录和迭代规范见[仓库组织方案](../docs/repository_layout.md)。

## 文档职责

每个当前实验只维护一套主文档。README 负责用途与导航；plan.md 负责已确认要求；method.md 负责实际方法、字段与指标；usage.md 只写已验证操作；conclusions.md 负责研究判断。状态、预算使用和下一步只写 docs/tasks/current.md，不在各文档复制进度表。

配置进入 configs，分析程序进入 analysis，实验自身的读取、统计和输入隔离检查进入 tests。平台通用能力及其功能检查仍在平台目录，不从实验导入。结果按研究问题进入 results，不再按多轮重复使用的 E1/E2 阶段名追加所有材料。

旧方案、方法和结果从当前实验的 archive 入口查找，绑定原始提交与适用范围。新的方案不追溯改变旧实验目标，历史原始数据和摘要不因整理而改算。新内容使用对应分区，不向实验根目录增加临时核查程序。

## 目录组织与历史复现

当前研究的程序、检查与配置分别位于 `analysis/`、`tests/` 和 `configs/`；旧机器结果位于[历史分区](scaleup_issue_screening/archive/README.md)。移动对应关系与等价检查入口保存在该索引，旧结果保持原内容。目录重构独立验收，不与研究逻辑变更合并，也不以目标规模仿真代替导入、命令、链接和结果等价检查。

历史命令通过其固定 Git 版本复现，不要求最新代码永远兼容每一个旧入口。需要继续使用的功能整理成明确的公共读取或分析入口，不增加成批兼容包装。只在实际有内容时创建子目录，不预建空脚本、空报告或空结果页。

## 数据和证据

版本化配置、方法和必要摘要提交 Git，真实来源与大型执行产物留在 data/、runs/ 等已声明位置。沿用运行清单记录版本、预算、输入与产物，不另建一套无法对照的清单制度。

研究者可以读取内部真值解释现象；待评价的诊断和采集方法只接收明确开放的信息。结果应能从结论、比较表追溯到运行清单与原始证据。采集信息收益、诊断规则改进、真实业务影响和硬件实现代价分别说明。
