# 完整普通DAG基线的首次提取检查

目的和预算见[事前协议](protocol.md)。[本轮结果](results/20261027/report.md)是一次失败与依赖转换缺项的诊断；没有产生普通DAG在三种网络下的完整性能结果。

仅执行原网络一次，256次默认交互耗尽，35,853ns终止。独立检查还发现native after字段遗漏应用issued事件的必要开始条件，使专家计数块提前100ns进入COMMIT，提前发布此前聚合的请求。这一输入不是完整通用应用事件DAG，不能用其误差证明一般DAG或其他仿真器做不到。

下一步应从完整事件图保留submitted/local_started门槛，复用现有有界回调批次，再进行配置迁移比较。应维持256交互限制，不增加固定100ns等待或使用参考绝对时间。该结论是本轮发现，不表示该后续实现已经完成。

原始参考与运行时来自已经固定hash的两个持久归档，无下载或构建。运行前可用`prepare.py --repository <仓库> --stage <不存在目录>`恢复；`run_one.py --repository <仓库> --stage <目录> --name baseline`只调用公开事务会话。原网络等价性没有通过，运行器会拒绝其余配置。本阶段已关闭，不应按旧预算重复网络。

离线检查不运行网络：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/static_dag_transfer/analyze_failure.py \
  --stage /path/to/evidence --output-dir /path/to/new-analysis
```

完整原始记录与可恢复包放在`runs/static_dag_transfer_20261027/`，内存盘原件为`/dev/shm/moe-static-dag-transfer-20261027/`。包包含已有原生运行时用于保存身份；离线复算不执行它，也不是新环境从零原生构建或GPU校准。恢复命令为`checkpoint.py restore --archive <包> --output <新目录>`；恢复前验证全部成员hash，结果按原始公开输出重算，不能把检查脚本成功当作网络成功。
