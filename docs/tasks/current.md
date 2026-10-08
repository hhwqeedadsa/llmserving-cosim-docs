# 当前任务与交接

当前任务：**Scale-Up 性能现象探索与被动测量需求验证**。

当前执行步骤：**发送槽等待的基础观测对照**。状态：`completed`。

2026-10-08 用户确认继续。围绕已发现的发送槽等待，比较端口计数与计数加队列两档输入；准备、测量与评价分开。既有三组单/双槽执行作为开发参照，规则冻结后生成 seed=3 的新输入对作复核，同时验收不依赖旧运行目录的用户流程。完成验证后本地提交，不推送。

## 范围与依据

按 [开发维护说明](../development.md)、[实验方案原文](../../experiments/scaleup_observability/plan_v0.1.md)及[实施入口](../../experiments/scaleup_observability/README.md)执行。本次主仓基线 `8acfa26`，主仓及 13 个递归子模块工作区干净，继续使用已验证的固定后端，不修改平台或依赖。

分析读取隔离后的公开观测，不读取内部依赖、发送槽配置、精确等待范围或配对反事实。评价资料由准备程序单独生成；图推导的就绪等待及槽占用只用于核验，不改名作为端侧可采集记录。所有 rank、出口及固定时间区间同时纳入，简单候选窗口规则和评价口径见[观测方法](../../experiments/scaleup_observability/observation-method.md)。

本轮只运行两个新增小场景，保留旧产物；不启动完整场景、全参数扫描、复杂分类器或新的平台记录能力。候选端侧事实记录需说明语义与实际源码位置，作为后续独立平台任务讨论。

## 完成结果与证据

已完成两档公开观测的隔离准备、固定规则测量、内部标签评价，以及 100 ns、1 µs、10 µs 和两种偏移网格的比较。新 seed=3 单槽/双槽输入独立完成完整用户流程；前三组旧输入仅用于开发参照。

25 项小型检查通过；两个新联合运行、导出及公开校验和十个准备/测量/评价阶段通过。13 个递归依赖核对一致。用户命令及输入输出见[观测操作](../../experiments/scaleup_observability/observations-usage.md)，方法、结果与任务状态分别维护，不追加到方案原文。

发现：计数筛选覆盖绝大部分槽等待，但仍需检查较大范围；本轮队列规则几乎没有额外缩小范围，原因仍未确定。新输入复现了双槽整轮更快和槽等待减少。具体数值、适用范围和取舍集中在[观测结果](../../experiments/scaleup_observability/results-observations.md)，版本化指标和资源索引见[结果 JSON](../../experiments/scaleup_observability/results-observations-v1.json)。

完整产物在 `runs/scaleup_observability/observations-v1/`，命令及检查日志在 `runs/scaleup_observability/observation-checks/`。一次摘要拼装错误已修正，失败日志保留；无仿真或测量失败重跑。

## 后续边界

建议下一项独立平台工作补充实际就绪待发 SEND 数、发送槽占用与容量的被动记录，并验证开关不改变执行。本轮只明确了采集位置及验收需求，没有实现该能力，也没有启动下一任务、复杂测量模型或完整场景。继续前由用户确认范围。

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

完成时累计产物约 37.800 GiB，本轮较预检增加约 48.1 MiB。没有提限、删除旧产物或自动重跑仿真；详细时间、内存口径和空间快照在本轮结果中维护。

## 前一任务

发送槽对照已在 `8acfa26` 完成，见[结果](../../experiments/scaleup_observability/results-slots.md)；九组正常小场景见[首批结果](../../experiments/scaleup_observability/results.md)。完成本轮不表示整体研究方案已完成。
