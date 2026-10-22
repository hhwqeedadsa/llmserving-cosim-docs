# Scale-Out 可靠性改进审阅入口

[整体说明 HTML](architecture.html)对应[报告原文](../../reviews/ScaleOut超时恢复与可靠性改进_20261022.md)，说明目标、约束、实际改动、结果和下一步。运行 `python3 docs/review_artifacts/scaleout_recovery_20261022/build_review.py` 从原文生成，使用本地样式与链接，无外部资源。

[五组实验 HTML](report.html)、[Markdown](results.md)和[数据摘要](summary.json)由[独立分析脚本](../../../experiments/scaleout_recovery/analyze.py)读取实际运行生成。来源平台为`0fb863a`，实验与报告提交为`210cf69`；没有将后续建议写成已实现。

[公开恢复示例](../../../examples/scaleout_rdma_recovery/README.md)提供可修改JSON和完整命令；[功能验收](../../../fixtures/scaleout_recovery/README.md)说明检查覆盖。这里保存必要摘要，大产物在运行根和本机忽略备份。

[verification.json](verification.json)核对15份构建源文件、链接和离线重复分析/HTML生成；[completion_audit.json](completion_audit.json)核对106份已有运行结果，其中41份启用恢复。未额外启动仿真。60项Python、3个纯C++和各实际功能门的证据在fixture。

[归档清单](archive.json)记录2823份普通文件的逐项hash检查、压缩包hash与本机备份路径。备份包含输入、成功/失败结果、命令、资源记录、外部依据、源码快照和构建证据；不包含整套原生依赖库。实际依赖版本、源码hash和二进制hash已登记，可从固定依赖重新构建。共享内存原件重启会丢失，持久目录中的压缩包不在Git，也未上传远端。

HTML内容与链接已核验，未做浏览器视觉验收。无千卡运行、真实网卡超时校准或生产可靠性精度结论。
