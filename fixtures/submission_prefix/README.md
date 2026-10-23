# 事务准备与前缀发布验收

该功能把已有无时间队列的准备/发布边界接到实际MRT1网络，并增加在线prepared回调。它没有改变平台默认，不是新网络算法或现代通信库时延校准。输入、输出和可复制运行方法见[用户小例](../../examples/scaleout_rdma_transactions/README.md#准备工作后统一发布)，限定要求见[规范](../../docs/tasks/submission_prefix_spec.md)，来源摘录见[sources.json](sources.json)。

`tests/integration/check_submission_prefix_workflow.py`通过公开CLI和直接原生入口独立核对：同连接20μs前无包、另一连接独立发送、完整前缀发布、原子旧值实际返回、源范围持续保护、在线prepared驱动后继、全false与默认结果去新增字段后相等。深度1、全聚合尾和fence不能代替发布均保留真实5ms未完成状态；7类非法声明在probe/session拒绝；错误COMMIT整批不注册。修改prepared时间、发布上界、首包时间三种篡改被检查器拒绝。详见[验收摘要](verification.json)。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B tests/integration/check_submission_prefix_workflow.py \
  --backend /ABS/TRANSACTION_PROBE --session-backend /ABS/TRANSACTION_SESSION \
  --old-backend /ABS/PRECHANGE_TRANSACTION_PROBE --output /dev/shm/NEW_PREFIX_CHECK
```

受影响事务、在线会话、稀疏窗口及Python输入检查分别记录在[回归](regressions.json)。隔离构建只编译受影响对象、复用固定原生库，前两轮原件均保留；[构建](build.json)描述最新两个入口。每命令60秒/RSS1GiB/地址2GiB，仿真5ms/结果16MiB，累计480秒/192MiB，tmpfs预留8GiB；不安装、下载、修改依赖或执行GPU，不重跑旧失败负载。

`checkpoint.py --root /ABS/THIS_RUN`保存并校验源码、小型输入、两入口及8库，在新源码根、空cwd及包内库上执行公开topology/probe/session三条命令。归档成员≤96MiB，压缩≤32MiB；恢复结果除库路径外相等，并独立核对实际字节。见[恢复](recovery.json)和[收尾](closeout.json)。原件及检查点在`/dev/shm/moe-submission-prefix-20261023/`，是易失存储，无持久备份；恢复仍需宿主Python和系统库。

仍不覆盖现代self-peer、GPU可见性、完整现代事件接入或生产RoCE恢复。全部聚合尾可能阻塞是此显式模型的边界，不能外推为真实GIN库漏洞；平台功能正确不证明论文创新或性能收益。
