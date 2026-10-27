# 网络确认能否代表应用可安全推进

这是一次创新筛选，不是新恢复协议或已确认漏洞。依据和边界见[事前方法](protocol.md)，结果见[中文报告](results/20261027/report.md)、[网页](results/20261027/report.html)及[摘要](results/20261027/summary.json)。

固定Linux v6.12的10个未修改函数/2个枚举，在明确替代的队列/时钟/驱动环境中检查5种请求前缀。结果收紧了此前异常：普通累计ACK在原子重发前到达时被posted状态挡住，重发后到达时仍可通过一个后续门槛；不能因此声称完整栈可达或旧值错误已在本轮复现。带FENCE的重试还受到自身保留额度影响，但没有执行真实定时器或证明死锁。

核对缓存的DeepEP Hybrid tail/slot调用后，暂不将该旧值分支作为本课题MoE跨机主线：远端tail通知接口不返回旧值，消费slot返回值的所核原子操作位于本地。本结论不能推广为所有MoE/provider都不需要返回值，也不能据此删除Atomic ACK或传输完成。整个目标的两项创新仍未成立。

当前上游核查首次GitHub请求返回HTTP200，但大提交详情超过512KiB上限，保留524289B检测哨兵及失败收据，关闭全部后续HTTP。不是连接失败，未重试或换API；当前版本是否修正仍未知，不用截断正文冒充commit/tree身份。旧来源重新核9blob/5tree；平台/依赖没有改变。

代码分别在 `analysis/sources.py`（有界来源）、`prefix.py`（本轮必要函数提取及唯一编译）、`relevance.py`（目标MoE调用匹配）、`verify.py`（独立源/状态表与篡改核验）、`report.py`（解释）及`recover.py`（离线归档/复制源码复核）。正式研究不导入平台tests/fixtures驱动，也不运行历史资源测量程序。

原始证据位于 `runs/rc_completion_reachability_20261027/`。来源获取和编译额度已经关闭；已有输入可仅离线复核：

```bash
PYTHONPATH=. python3 -B -m experiments.rc_completion_reachability.analysis.verify \
  --stage runs/rc_completion_reachability_20261027 --output <新的JSON文件>
```

该核验不会编译、联网或执行ns-3/GPU。逐成员hash归档与复制源码/空cwd复算见[恢复](results/20261027/recovery.json)，资源及失败事实见[收尾](results/20261027/closeout.json)。它们保证本有限证据可复查，不证明漏洞、原创性或性能收益。
