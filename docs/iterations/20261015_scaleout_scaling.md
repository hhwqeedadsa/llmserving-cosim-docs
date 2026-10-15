# 2026-10-15：Scale-Out 规模与热点研究设计

本轮在分支 `work/scaleout-20261015` 继续非UB路线。开始版本 `b26e211`，公共容量功能提交 `50c94da`；本记录与实验/文档一同保存为后续研究提交，可用 `git log --oneline -- docs/iterations/20261015_scaleout_scaling.md` 查询。本地保存，不push。后续动态状态只维护[当前任务](../tasks/current.md)。

## 问题与限定条件

用户要求说明千卡仿真、batch、All-to-All与EP/DP选择，研究真实MoE训练热点，并规范third_party和每轮迭代。原始JSON路径稍后提供，因此本轮完成独立核算和设计，不下载大型数据、不产生真实热点统计。非UB在线服务和完整训练前端尚未实现。

## 改动与证据

| 交付 | 入口 | 验证范围 |
|---|---|---|
| 公共核算CLI | [示例](../../examples/scaleout_capacity/README.md) | 1024GPU/EP64、batch与阶段、两种通信模板、固定数组；非网络运行 |
| 真实阿里ns-3内存探针 | [实验](../../experiments/scaleout_scaling/README.md) | 固定commit编译、创建0/8个交换机，统一脚本再次运行 |
| 网络与热点报告 | [报告](../reviews/scaleout_scaling_hotspots_20261015.md) | 千卡路线、DP命名、训练四程与梯度同步、多rail、数据适用范围及对照 |
| 框架规范 | [组织方案](../repository_layout.md) | 当前问题、目标责任、渐进迁移与验收；无批量移动 |
| 检查与运行索引 | [摘要](../../experiments/scaleout_scaling/validation.json) | 13项相关单元检查、公开CLI、探针、链接和依赖检查 |

关键发现：固定1025²×8个uint32数组在小端口交换机上也实际占用约32MiB；8对象RSS增约258MiB。千卡示例的192个Ethernet/NVSwitch对象仅数组推算约6.01GiB，尚未测量完整运行。按实际端口分配是后续优化方案，不是已完成收益。

已有HF清单对应R1-AWQ/MMLU推理路由，并非训练step演化记录。现有平台已有真实路由读取，不能笼统归为random。专家频率、实际NIC负载及拥塞需分层验证；b从1增4时线性字节增长在固定模板中是定义推论，不等于研究创新。

## 后续最小验证项

先修复非UB完成边界并做小规模incast/All-to-All-v共享网络基准；真实路由恢复后沿用已有校验/读取器做分层统计；训练结论需要新增有step、微批及accepted/drop等字段的训练来源。阶梯测量16/64/256/1024的CPU、RSS、事件和日志成本，再选择优化。独立运行耗时不可相加冒充联合执行。
