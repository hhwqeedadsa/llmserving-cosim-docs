# 当前任务与交接

当前执行项：**v0.4 平台能力 M11–M16**。状态：`completed`。

2026-10-06 用户授权连续完成本阶段实现、验证、记录及本地提交，内部步骤无需逐项确认；本阶段不新增多 TP。平台验收已经完成。用户随后授权本次远程推送，发布已验收的实现与记录；不启动映射价值实验或完整 64 轮服务。

## 状态与证据

| 工作 | 要求与设计 | 状态 |
|---|---|---|
| v0.4 平台能力 | [归档增量规范](../archive/moe_ub_cosim_dev_v0.4.md) | `completed` |
| 映射价值验证 | [实验说明](../../experiments/mapping_value/README.md) | `pending`；正式实验尚未启动 |

| 编号 | 已完成范围 | 证据 |
|---|---|---|
| M11 | STORE/LOAD 图依赖、真实供数通知、请求与数据方向、完成及资源退出；保留 WRITE | [原生操作](../../fixtures/m11_native/README.md) |
| M12 | 原生逐包喷洒，源端跨平面与交换出口候选，实际分散、乱序及被动性 | [路径检查](../../fixtures/m12_native_routing/README.md) |
| M13 | 端侧准备成本、物理有向出口服务区间，以及受限网络重放覆盖 | [成本与出口](../../fixtures/m13_endpoint_service/README.md) |
| M14 | 实际操作阶段、纯 VOQ/FIFO 等待面积、均值和峰值 | [阶段与队列](../../fixtures/m14_operation_queue/README.md) |
| M15 | 直接聚合、有限详细记录、分类消息真值、采样能力声明及旧数据读取 | [保存与采样](../../fixtures/m15_aggregate_capture/README.md) |
| M16 | 独立公开用户流程、组合回归及代表性完整原生首轮 | [最终证据](../../fixtures/m16_native_target/README.md)、[摘要](../../fixtures/m16_native_target/result.json) |

M16 的 128 rank、1024 独立请求来源、61 块完整图使用 STORE/LOAD、逐包喷洒和 CBFC，在一个共享网络中完成；独立核对业务字节、请求方向、回调、状态回收和所选端口记录通过。完整执行只覆盖首个 Decode 入口；完整 64 轮、目标硬件校准和第二次完整原生目标重放未被记录为通过。

无需历史数据的小型[原生示例](../../examples/native_first_round/README.md)通过 14 条公开命令，包含相同重放等价、出口限速反馈与非法采样拒绝。WRITE 用户流程及原有详细数据兼容检查通过。包版本与命令版本一致为 `0.4.0`。

## 资源、失败和产物

重型阶段串行。完整目标阶段限 RSS 48 GiB、地址空间 56 GiB、7200 宿主秒；小型及导出阶段限 RSS 8 GiB、地址空间 12 GiB、1800 秒。累计磁盘限 32 GiB，保留文件系统至少 20 GiB 空闲，统计以下六个根：

```text
build/v04-platform                 runs/v04-platform
build/m10-resource-efficiency      runs/v03-resource-efficiency
build/m10-research-slice            runs/v03-research-slice
```

首次完整后端在仿真时间 0、业务激活前因读取器与传输 JSON 同时驻留超出 RSS 限制而停止；未提高预算。调整对象生存期后，原前缀全部执行和端口记录一致，公开回归通过，再使用同一完整图完成验收。失败、构图估算偏差、检查命令错误及验证依据保留于 [preflight.json](../../fixtures/m16_native_target/preflight.json) 和 [feeder-lifetime.json](../../fixtures/m16_native_target/feeder-lifetime.json)。最终结果以 `result.json` 为准，不改写历史检查点。

大文件均在忽略目录。完整原生首轮复用入口为 `runs/v04-platform/m16-target-slice/`，实际公开采样为 `m16-target-sample/`，配置与核验归档为 `m16-target-package/`。展开图及登记副本已逐文件核对后回收；采样保留有限执行缓存。准确资源、命令、输入、退出状态、日志和范围只在 M16 fixture 及对应运行目录维护，不复制到本页。新检出不能假定已有这些数据；获取与操作说明见 [CM384/R1 场景](../scenarios/cm384_r1.md)及[用户指南](../user_guide.md)。

## 提交与停止位置

主仓实现按 M11–M15 以及 M16 检查点保存，M16 核心实现为 `5a6ff32`、读取器生存期修复为 `ff50242`；最终封存、规模证据和文档保存于 `f96ea80`。ns-3-UB 固定 `c0f1fe54245203a3b14468e44e893a38792cfcc8`，LLMServingSim 固定 `9aed949c8e66fd1424d48365af64bfc0fe0b2ed1`，其他依赖按父 gitlink 与锁文件保留。

2026-10-06 按用户授权先将 ns-3-UB 的 3 个新增提交推送到 `baoy21/ns-3-ub:cosim`，再将主仓截至 `f96ea80` 的 9 个新增提交推送到 `baoy21/moe-ub-cosim:main`；均为普通快进推送，并读取远端分支核对提交。LLMServingSim 和 ASTRA-sim 没有新增提交，公开远端的 `cosim` 分支仍分别指向 `9aed949`、`e2a14f5`；其余递归依赖保持固定版本，没有推送或升级。主仓推送同时通过 Git 的子模块发布检查，13 项本地依赖检查通过。本次发布记录随本页提交同步，实际命令、退出状态、远端核对和凭证查询失败后改用公开只读核对的记录位于 `runs/v04-platform/publish-20261006T140304Z/`。大型输入、仿真产物和本机配置未纳入提交。

结束检查包括差异、修改文档的本地链接、规范归档正文一致性、公开命令版本以及 13 项固定依赖检查；详细命令和结果放在 `runs/v04-platform/m16-final-checks/`。普通文档收尾不重跑完整仿真。

平台能力与研究实验分别实施、提交和验收。本阶段没有阻塞；下一步候选是按独立实验说明准备映射价值实验，需用户确认后更新本页目标再开始。实验不得直接复用测试驱动、fixture 或本次专用资源测量脚本作为研究流程，也不得把功能通过或来源真实等同于硬件校准。

历史交付入口见[里程碑](../milestone_history.md)，文档职责与保存规则见[开发维护](../development.md)。
