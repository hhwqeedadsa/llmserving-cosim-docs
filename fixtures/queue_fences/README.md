# 原生工作队列前缀快照验收

本增量将已有GinWorkQueue的固定前缀查询接入原生MRT1实包执行：应用操作在前驱满足时捕获提交GPU所拥有的选定连接，等待当时已预留的工作完成，再提交后续操作。此前原生事务只能逐项引用操作事件或读取接收窗口，缺少这种不会被未来工作扩张的多连接等待。公开输入、字段和预算见[事务示例](../../examples/scaleout_rdma_transactions/README.md#等待调用时已有的工作前缀)。

## 验证结果

- [verification.json](verification.json)：新cwd的8条公开CLI、2次预期拒绝；正常6事务/52源数据包/47,960B有效负载。独立struct解码每个实际源包、重放目标内存、核对每个覆盖前缀和完成时刻，不导入平台实现作预期。
- 第一次捕获只覆盖操作0/1；之后在相同0ns应用回调提交的操作3不扩大它。第一次完成13,690ns，操作3完成16,413ns。它们相差2,723ns，表明两个完成条件不同；没有运行“错误屏障”反事实，不能把差值当作加速或业务收益。
- 第二次捕获覆盖连接0的0/3、连接1的1/4，按这些工作的实际完成结束。原队列即使先前空闲也保留对象和序号，所有连接共用同一Simulator。
- 深度1对照将操作3先预留再捕获：该工作尚不能发布，但仍被包含，必须等其真实ACK后才能结束。此对照同时改变提交顺序和深度，仅用于功能区分，不用于单因素性能解释。
- 空快照对照将fence放在生产者之前，捕获0并立即满足，不错误等待未来工作。此输入故意没有生产者汇合，**不是正确的数据就绪屏障**。
- 正常重复结果逐字节一致，应用id改名不改变线上字节与时间；非本地连接和重复选择在创建输出前拒绝。
- [local_regression.json](local_regression.json)、[transaction_regression.json](transaction_regression.json)：两类既有实包流程共16条CLI回归通过，保留错数据/少计数不放行、原子回复和跨包检查。
- [maintenance.json](maintenance.json)：7项输入检查、增量原生构建、资源和来源hash；[archive.json](archive.json)保存原始流程、失败日志及实现快照的本地检查点，逐文件hash复核。

## 与真实库的关系

固定NCCL GDAKI的flushImplModeCore读取各peer的sq_rsvd_index并等待对应CQ位置，支持“只覆盖调用时取得的位置，已预留工作也可能尚未完成准备”这一约束。DeepEP屏障先汇合/flush各上下文，再用上下文0通知；GIN API的源安全与目标可见性、跨NIC保证及代价仍分别解释。具体来源见[source_basis.json](source_basis.json)。

本入口是显式功能选择：多个源端队列在一个ns-3回调中取得快照，完成条件来自MRT1请求ACK及原子返回。真实GDAKI协作线程逐peer读取的位置不保证全局同时，CQ模式、GPU内存序及源buffer不在本次范围内。功能名queue_fence有意与真实GIN flush区分；没有现代DeepEP在线执行、self-peer GIN成本、RoCE原子协议或实机校准。本增量不是论文创新证明。

## 复核

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_queue_fence_workflow.py \
  --backend /ABS/NATIVE_TRANSACTION_PROBE --output /dev/shm/my-queue-fence-check
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p scaleout_transactions_test.py
```

使用公开rdma-build得到的transaction_probe或对应增量构建，不能使用UB程序。每命令60秒、RSS1GiB、地址2GiB，产物64MiB/累计128MiB，卷预留8GiB；实际命令、cwd与资源在maintenance记录。此检查不安装工具或修改依赖，不触发全模型/千卡实验。
