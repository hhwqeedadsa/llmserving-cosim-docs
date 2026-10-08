# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。

当前执行步骤：**三组固定输入的发送槽 1 与 2 对照及等待核验**。状态：`completed`。后续工作尚未启动。

2026-10-08 用户确认继续发送槽对照。本次复用经核验的三组每 rank 8 请求基线，串行完成三个双槽运行和已有事件中的等待核验；验证后本地提交，不推送。未启动其他参数扫描、观测比较、完整场景或平台模型开发。

## 阅读与实施依据

先读 [AGENTS.md](../../AGENTS.md)和[开发维护说明](../development.md)，再读[实验方案原文](../../experiments/scaleup_observability/plan_v0.1.md)及[实验实施入口](../../experiments/scaleup_observability/README.md)。原方案负责研究问题、阶段与取舍；实施入口只补目录和程序职责。

本次接手主仓基线为 `3d95f95`。主仓及 13 个递归子模块工作区干净，继续使用 `build/v04-platform/output/cosim/ns3.44-cosim-service`；六次执行的平台源码和后端摘要一致。依赖按父仓 gitlink 与 `deps.lock.json` 使用，本次没有平台或依赖修改，也没有重新构建。

## 完成范围与证据

| 项目 | 要求 |
|---|---|
| 小场景 | 8 rank、两台服务器、两个平面、4 个 Transformer 块，一次完整 Decode 执行 |
| 输入 | 三组固定请求与专家选择输入，均为每 rank 8 请求、总请求数 64 |
| 唯一变化 | 每 rank 发送槽从 1 改为 2；原生 worker、在途请求上限和请求标识池不变 |
| 设备与协议 | 相同参考配置、原生 STORE/LOAD 与逐包喷洒 |
| 配对检查 | 重新解析和核对容量；输入只改变发送槽，生成的计算/通信依赖图和实际工作量一致 |
| 观测 | 全部 32 个交换机出口采用同等计数和 VOQ/FIFO 记录；小场景详细资料用于研究者核验 |
| 等待核验 | 用 SEND 节点和传输提交/释放记录还原槽占用，分解全部开始前等待并保存最长五例及跨运行匹配 |

输入控制及判断边界见[发送槽对照方法](../../experiments/scaleup_observability/slots-method.md)，真实用户入口见[发送槽命令](../../experiments/scaleup_observability/slots-usage.md)。18 项小型检查、三个新增联合运行及导出/公开校验、六次执行的配对和等待核验均通过。另在 `runs/scaleup_observability/slot-user-check/` 从新目录生成三个单槽及三个双槽运行，已验证公开流程不依赖历史产物，且三对数值、节点时序与等待明细一致；这些重复运行属于用户入口验收，不增加研究样本。

发现与下一步取舍集中在[发送槽结果](../../experiments/scaleup_observability/results-slots.md)，版本化摘要及资源索引见[结果 JSON](../../experiments/scaleup_observability/results-slots-v1.json)。完整新研究产物在 `runs/scaleup_observability/slots-v1/`，实际命令与交付检查在 `runs/scaleup_observability/slot-checks/`。单槽参考资料仍在 `runs/scaleup_observability/normal-v1/`，原目录保持不变。

## 后续推进

本次对照已形成发送槽限制推进的具体证据及端侧等待减少、网络积压增加的取舍。后续建议围绕“尚无可发工作”和“有工作但发送资源占满”的区分梳理最小观测需求；推广结论前还需新输入或计算/通信节奏变化的复核。具体范围待确定，不自动展开全参数扫描或测量算法实验。

必要的内部记录或端点服务模型由具体发现驱动，作为独立平台工作定义输入、执行语义和最小验收，分别提交；实验统计和解释仍在实验目录。平台缺陷先用最小用例修复，受影响结果随后重跑。

完整 128 rank、61 块首轮仅在小场景形成明确发现后选择少量代表情况，放在最后；完整 64 轮不属于本阶段门槛。测量方法的观测比较与研究者内部核验分别组织，原方案之外的映射或复杂模型不自动引入。

## 资源与执行记录

沿用上一任务已登记的上限，不因新增实验目录重新获得独立的磁盘额度。以下为预算，不是本环境可用资源的实测结果。

| 执行范围 | 驻留内存 RSS | 地址空间 | 单阶段时间 |
|---|---:|---:|---:|
| 小场景、导出与分析 | 8 GiB | 12 GiB | 1800 秒 |
| 最后选定的完整后端 | 48 GiB | 56 GiB | 7200 秒 |

重型运行串行，累计磁盘不超过 48 GiB，文件系统至少保留 20 GiB 空闲。追加运行前核对实际占用，累计范围包含旧目录和新实验根：

```text
build/v04-platform                 runs/v04-platform
build/m10-resource-efficiency      runs/v03-resource-efficiency
build/m10-research-slice            runs/v03-research-slice
runs/mapping_value                 runs/scaleup_observability
runs/task-sync-20261008
```

新增缓存或构建目录也计入累计范围。预算不足时保留具体占用和阻塞，不自动提高限额、删除旧产物或缩减已声明负载来冒充完成。上限来源保存在[前一任务记录](https://github.com/baoy21/moe-ub-cosim/blob/9e9085b198efeaf10707b0ce38ea943aa75156bf/docs/tasks/current.md)。

实际命令、工作目录、输入及版本、退出状态、资源峰值和日志放在 `runs/scaleup_observability/`，必要配置和可复核摘要进入实验目录。本页仅更新当前状态、完成范围、阻塞与证据入口。完成并验证后按既有约定创建本地提交，执行成果发布仍按对应授权处理。

完成独立用户流程核验后，登记产物累计约 37.753 GiB，较本轮预检增加约 164.4 MiB；无资源超限、失败仿真重跑或旧产物清理。阶段耗时、内存采样限制与空间快照只在本轮结果和数值摘要中维护。方案原文字节摘要未变，同步证据保留在 `runs/task-sync-20261008/`。

## 前一任务

首批九组正常小场景保存于 `3d95f95`，发现与证据见[首批结果](../../experiments/scaleup_observability/results.md)。完成本次发送槽对照不表示整体研究方案已完成。

端网映射价值实验已完成并发布，设计、程序和结果保持不变，见[实验结果](../../experiments/mapping_value/results.md)和[里程碑](../milestone_history.md)。其完整任务记录保存在上述固定版本链接，不在本页重复复制。
