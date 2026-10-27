# 多路径预测边界与真实连接需求

本研究只做离线需求核算，不执行网络。固定八份真实MMLU路由的batch4/8、EP4，比较1/2/4核心图中持久连接的确定性ECMP需求与最短路径等分参考；完整范围和预算见[事前协议](protocol.md)。

原生ns-3已有ECMP、拥塞反馈、选择重传和故障。此前逐链路快速预测要求唯一最短路，其拒绝多路径不意味着后端没有该能力。阿里缓存源版本也已有上述主要网络机制；不能据此宣称集成这些机制就是创新。

四核心条件中，Dispatch数据阶段的最大链路帧服务量为等分参考的2.667/2.000倍，Combine为4.000/3.333倍（分别batch4/8）。这些是固定输入哈希的离线投影，既不是实测时延，也不是可避免收益或随机哈希期望。原先单核心条件两参考完全相同。普通精确哈希即可解释需求差异，应作为下一轮强基线；尚无新算法收益或两项论文创新。

预测只接受observed，不导入布局/控制器/fixture/测试。核验从已接受旧执行独立重建67个连接和200项远端操作的请求/回复需求，以小图完整简单最短路径枚举核对795组路径和2895次哈希键；四类篡改拒绝。多路径图没有新包级执行，不能把源码定义的投影写成实际包轨迹验证。

使用已接收来源与固定前阶段归档，在不存在的新目录运行：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m experiments.ecmp_baseline_audit.run \
  --repository /path/to/moe-ub-cosim --output /path/to/NEW_ECMP_AUDIT
```

执行器只调用三次公开topology命令和独立离线程序，不启动后端。每条命令通过公开资源监控限制，失败关闭原执行账目。

首次核验因遗漏put的注册内存source长度而失败，源码与日志已保留；修复后只重算已有预测的核验，没有重做成功准备、拓扑或预测。最终验证另补完整哈希值相等断言；复制源码恢复时执行该断言，结果仍须逐字节相同。原执行账目保持failure_preserved，最终阶段结果与此失败分别记录。

报告见[Markdown](results/20261027/report.md)、[网页](results/20261027/report.html)、[独立核验](results/20261027/verification.json)及[恢复](results/20261027/recovery.json)。完整原件与带hash归档保存在`runs/ecmp_baseline_audit_20261027/`，原始JSON不进Git。恢复不需要平台包、动态库、原仓库cwd或GPU：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m experiments.ecmp_baseline_audit.checkpoint restore \
  --archive /path/to/checkpoint.tar.gz --sha256 RECEIPT_SHA256 \
  --output /path/to/NEW_RECOVERY
```

先核包hash、成员类型/路径/大小及逐成员hash，再写目录；复制源码/空cwd重算所有预测、独立核验及两种报告，零新网络。复现流程不能重开已关闭的本轮预算。
