# 当前任务与交接

当前执行项：**端网映射价值实验**。状态：`completed`。

2026-10-07 用户确认小场景与完整首轮参数，授权连续完成各批次、验证、记录及本地提交，随后明确授权推送 GitHub。本任务已完成并发布，停止于已确认范围；后续功能或实验另行确定。

## 目标与结果

检验已知正确端网关系的分析价值，区分端侧准备、物理出口服务、局部与整轮影响、业务与应用状态关系。准备、分析和评价分别限制输入；各信息条件来自同次物理执行，分析不读取真值或干预声明。没有开展映射恢复算法、多 TP、64 轮服务或硬件校准。

平台 M11–M16 已完成并发布，见[里程碑](../milestone_history.md)和[原生首轮验收](../../fixtures/m16_native_target/README.md)。实验从主仓 `0ade7a8`、UB `c0f1fe5`、LLMServingSim `9aed949` 的固定依赖开始，本任务没有修改平台或依赖源码。

| 批次 | 已完成内容 | 状态 |
|---|---|---|
| 1 | 独立 8 rank、32 请求、4 块小输入；公开运行入口及准备/分析/评价隔离 | `completed` |
| 2 | A：端侧准备与物理出口基本对照；保留正常、未命中及无明显整轮影响 | `completed` |
| 3 | B：局部与整轮影响；C：业务与应用状态关系；重叠负对照 | `completed` |
| 4 | 少量出口队列增强，按统一规则汇总九次小场景的七种信息条件 | `completed` |
| 5 | 128 rank、1024 个真实路由来源、61 块首轮的正常及两档限速；五种信息条件、资源证据与复现交付 | `completed` |

九次小场景和三次完整首轮均完成公开执行、导出、校验和对应分析评价。完整场景中，业务映射没有缩小候选出口数，少量队列信息改善了排序；保留非单调整轮影响及无收益结果，不据此调整干预重跑。具体数值与解释只在[实验结果](../../experiments/mapping_value/results.md)维护。小场景采用合成路由，完整场景采用固定真实来源；计算成本仍是未校准参考值。

## 检查与资源

20 项边界、成对输入和冻结检查通过。独立目录只放分析代码及封闭输入，使用禁用环境与第三方包路径的公开分析命令，结果与原输出逐字节一致；这是复现检查，不是操作系统安全隔离。完整执行的 39 个资源监测阶段全部成功，退出状态与指标独立核对通过。

小型、导出及分析阶段的驻留内存（RSS）上限为 8 GiB、地址空间 12 GiB、单阶段 1800 秒；完整后端上限为 RSS 48 GiB、地址空间 56 GiB、7200 秒。重型阶段串行，文件系统至少保留 20 GiB 空闲。用户明确允许累计磁盘上限由 32 GiB 改为 48 GiB；计入以下旧产物及新实验根：

```text
build/v04-platform                 runs/v04-platform
build/m10-resource-efficiency      runs/v03-resource-efficiency
build/m10-research-slice            runs/v03-research-slice
runs/mapping_value
```

累计磁盘采样峰值 37.52 GiB，收尾快照 37.48 GiB；完整后端 RSS 采样峰值 45.59 GiB。没有提高内存或单次时间上限，没有删除旧产物；展开图共享缓存保留。初次小输入容量预检失败、原 32 GiB 完整预检不足及原因均保留，入口见结果文档。

## 交付与证据入口

- [实验说明](../../experiments/mapping_value/README.md)：研究问题与设计；[分析规则](../../experiments/mapping_value/method.md)：输入边界、判断方法与限制。
- [公开命令与复现步骤](../../experiments/mapping_value/usage.md)：用户入口、配置和输入获取规则。
- [结果与限制](../../experiments/mapping_value/results.md)：机制比较、完整对照与资源消耗。
- [小场景摘要](../../experiments/mapping_value/results-small.json)、[完整场景摘要](../../experiments/mapping_value/results-full.json)、[完整证据摘要](../../experiments/mapping_value/results-full-evidence.json)：条件结果、输入摘要、资源及检查绑定。

实际命令、工作目录、输入、退出状态及日志位于 `runs/mapping_value/`：小场景为 `batch1-v2/` 至 `batch4/`；完整执行、冻结干预、分析评价及汇总为 `full/`；检查为 `full-commands/` 和 `delivery-closed-analysis/`。大文件不提交 Git，可复用程序、配置及必要结果摘要在实验目录。

实验成果的五个提交 `f598f45` 至 `59e00cd` 已按用户授权推送至 GitHub 主仓 `origin/main`；本次没有修改子模块或依赖指针。发布命令、退出状态及远程核对记录位于 `runs/mapping_value/publication-20261007/`。
