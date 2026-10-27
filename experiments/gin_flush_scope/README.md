# GIN完成等待与大EP适用性

固定版本的源码约束与有限事件筛选；不是网络性能或漏洞实验。

- [冻结协议](protocol.md)
- [报告网页](results/20261027/report.html)、[文字与来源](results/20261027/report.md)
- [独立核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)、[资源收尾](results/20261027/closeout.json)

入口为Python标准库模块：`analysis.study prepare/execute`、`analysis.verify`、`analysis.report`及`analysis.recover`。使用`python3 -B -m experiments.gin_flush_scope.analysis.study --help`查看参数；从主仓库运行时设`PYTHONPATH=python:.`。原始执行命令和限制保留在运行目录各`*-monitor/probe.json`，先阅读协议并使用新的输出根及明确预算。复现历史结果使用归档与离线恢复，不重新执行已关闭研究条件。

报告区分provider peer、rail peer、context索引、完成前缀及任务完成；普通动态资源DAG、flushAsync和通道调参均是强基线。17份路由的来源和15份完整可用性沿用既有接收记录，本轮不重新统计或复制路由。

首次执行在源码定位时发现flush/fence重复循环而停止，尚未计算六配置或事件条件。原定位器/失败状态与monitor保留；按协议更正只限定函数范围，通过`continue-unstarted`完成未开始部分，未重复准备、重跑成功条件或扩预算。报告结果不覆盖这条失败记录。
