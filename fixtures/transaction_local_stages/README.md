# 接收条件、本地处理与后续事务验收

本增量使用`moe-rdma-transactions/2`，将实际接收窗口的条件消费、本地声明处理和后续发送接到同一原生ns-3时钟。输入和操作见[公开示例](../../examples/scaleout_rdma_transactions/README.md#接收条件与本地处理交替执行)。旧v1保持，未修改固定依赖或执行GPU。它是功能能力，不能作为论文优化收益或现代GIN成本基线。

## 复核

在仓库根目录执行；两个可执行程序分别为新构建和保留的v1参考版本，输出父目录须存在且至少预留8GiB：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/integration/check_transaction_local_stage_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-transaction-probe \
  --reference-backend /absolute/reference/ns3.42-moe-rdma-transaction-probe \
  --output /absolute/new-output-directory
```

检查程序从新的cwd调用公开CLI，不从原运行目录取输入。`check_remote_transaction_workflow.audit`用独立struct解码器重建实际源包的MRT1请求/返回值，按接收/消费order重放窗口；混合节点的消费不会被过滤掉。部分结果检查只支持“所有已提交远端事务已完成”的三个固定反例，不声称覆盖任意在途截止。

端点检查通过实际packet_uid还原目标RX，再追踪目标发出的精确前缀ACK到源端；采集不含ACK端口，故明确要求同端点/前缀的工作不重叠，且首个ACK候选唯一。当前人工例通过该条件；它不是一般多流端口解码器。

## 结果

- 主流程24条公开CLI、9条原生命令：10个远端事务、6个本地节点、14个源包/560B载体负载，14帧的实际端点RX/ACK通过。GPU4首次条件在18,096ns入队并清零，等待至106,042ns才开始，146,042ns完成；不提前发后继通知。结束714,304ns，包括500,000ns声明的本地尾部。
- 独立FIFO例验证已排队工作优先于输入位置更早但刚解锁的节点；零时长经过真实调度事件，跨GPU处理重叠。
- 少计数/错数据：均只完成3个事务、7个后继未提交、无条件消费。5ms截止：GPU4长处理未完成，另一本地节点已消费却仍排队；后继不发送、不补造完成。
- 重复结果逐字节一致；id改名不改变实际包/负载。work/local-waits/queue-fences三组v1新旧二进制逐字节一致；只有remote的v2去掉四个新增说明字段后等于v1。
- 9类输入分别经CLI和原生拒绝，无输出结果；10项输入单元检查、三组旧流程共24条CLI、13项固定依赖检查通过。

摘要见[主验证](verification.json)、[回归](regression.json)、[构建](build.json)、[中文报告](../../docs/review_artifacts/transaction_local_stages_20261023/report.html)。第一版流程后补强ACK候选不重叠断言并再验受影响流程；没有改网络实现或放宽断言。初次文档补丁因标题不匹配未应用，修正后写入。

## 保存与限制

原始命令、cwd、退出状态、输入、日志及资源监测在`/dev/shm/moe-transaction-stages-20261023/`。这是易失内存盘，**无持久备份**。[归档](archive.json)与[恢复](replay.json)说明固定运行时、源码及包内ns-3共享库的核验范围；[收尾](closeout.json)记录预算和旧可执行程序不变。没有承诺完整依赖离线源码构建或跨系统镜像。

入队消费不保护排队期间的数据，也不验证CUDA内存序、输入buffer租约或数值计算；处理耗时仅为声明值。全局applied编排不等于源端可知状态。现代DeepEP事件、provider完成、self-peer物理路径、匹配硬件校准和千卡完整应用仍未接入。本增量不能证明真实MoE长尾、算法收益或论文主贡献。
