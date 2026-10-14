# Scale-Up 性能问题与被动诊断能力研究

本目录是当前研究入口。使用现有 MoE 超节点场景，检查本地竞争与服务不足、下游阻塞与排队传播、多平面推进差异，再比较已有观测、较充分观测和受预算约束的被动采集能够完成什么判断。

目标是找到可信且有用的诊断与测量问题，不要求先发现全新瓶颈，不以端网映射或时间同步为预定答案。负载、拓扑、计算成本和通信基线沿用既有约定；C1 发送队列分支冻结，N1/N2 和通信结束边界作为历史参照。

## 从这里开始

| 需要了解 | 唯一入口 |
|---|---|
| 研究要求与完成范围 | [实验方案](plan.md) |
| 正在做什么、预算用了多少、停在哪里 | [当前任务](../../docs/tasks/current.md) |
| 已有哪些研究判断及限制 | [研究结论](conclusions.md) |
| 实际字段、诊断方法与评价定义 | [方法说明](method.md) |
| 已验证的操作与历史复现 | [使用说明](usage.md) |
| 实际出口竞争、服务与业务作用 | [本地服务结果](results/local_service/README.md) |
| 信用阻塞、业务传播及未覆盖机制 | [下游传播结果](results/backpressure/README.md) |
| 实际来流、平面服务与残余完成 | [多平面结果](results/multipath/README.md) |
| 19 个诊断条件、采集成本及机器表格 | [测量预算比较](results/measurement_budget/README.md) |
| C1、N1/N2、结束边界和初始探索分别是什么 | [历史归档](archive/README.md) |
| 与另外两个实验目录是什么关系 | [实验总索引](../README.md) |

方案规定机制覆盖、正常分析、受控验证和采集成本比较的范围。结果按研究问题分类，最终在研究结论中给出去留判断；执行状态只维护在当前任务页。

## 目录与实现边界

`analysis/` 保存准备、分析和评价程序；`tests/` 保存合成检查及检查摘要。配置按固定输入 `configs/baseline/`、场景及干预 `configs/scenarios/`、观测与导出 `configs/observation/` 分区。

历史结果位于 `archive/initial_screening/`、`archive/sender_queue/`、`archive/network_cases/` 和 `archive/operation_boundary/`；跨分支补证保留单一原件。原始文件对应关系见[迁移索引](archive/README.md)。历史完整命令使用固定版本；当前模块统一从 `experiments.scaleup_issue_screening.analysis` 进入，检查模块从 `.tests` 进入。

平台运行、记录与模型语义分别见[用户指南](../../docs/user_guide.md)、[记录格式](../../docs/record_storage.md)、[模型依据](../../docs/model_validation.md)。实验只使用公共命令与稳定读取接口，平台不导入本目录，诊断不能默认读取 truth 或原始执行图。

大文件与日志留在 runs/scaleup_issue_screening/ 的已声明位置。历史完整文档由固定提交保留，不在 README 复制结果表或运行日志。新目录和结果页在实际有内容时创建。
