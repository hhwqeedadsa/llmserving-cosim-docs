# 历史实验：早期性能现象与观测探索

本目录保存 A3 资源修正前的性能现象探索，涉及正常小场景、全 rank 发送槽、端侧状态、LOAD 响应拆分、路径和出口服务，以及少量完整参考场景。部分现象依赖旧发送资源、拉取式通信和转发假设，不能直接解释为当前 CANN 或目标硬件中的瓶颈。

当前研究入口为[Scale-Up 性能问题与被动诊断能力](../scaleup_issue_screening/README.md)，整体关系见[实验总索引](../README.md)。本目录保留历史代码、配置和数据，不再按其中各项下一步建议自动开新实验。

## 阅读和复现

先读[历史研究总结](conclusions.md)。原方案、每一批方法和操作的完整导航见[封存版本的 README](https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_observability/README.md)，初始方案仍在[plan_v0.1.md](plan_v0.1.md)。这些定义属于当时任务，不是当前方案。

复现使用原运行的固定提交、依赖和输入；不通过改成新资源模型后覆盖旧结果。本次只调整入口说明，未移动代码、配置或机器结果。
