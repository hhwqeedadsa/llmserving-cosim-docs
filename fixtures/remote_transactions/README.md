# 远端内存事务功能验收

本增量将远端地址、访问密钥、写入数据和原子增量作为实际载体报文传输，接收端解码后应用目标窗口，原子旧值再经同一张原生网络返回。它是MRT1功能协议，不是现代GIN provider或RoCE性能模型。用户接口、帧格式、完成条件与预算见[公开小例](../../examples/scaleout_rdma_transactions/README.md)。

## 证据

- [verification.json](verification.json)：新cwd的公开流程，独立struct解码实际源包、重放目标状态、对照完整返回值与最终内存。默认6操作/10包/416B；64B载体MTU的跨包put共12包/545B；另检查深度2并发根工作、重复、应用id重命名和两类拒绝。深度2同时改变根依赖，不是单因素性能实验。
- [maintenance_checks.json](maintenance_checks.json)：严格C++17核心检查、输入检查、构建、原持久字节流程回归、固定依赖状态及来源hash。
- [source_basis.json](source_basis.json)：固定NCCL GDAKI、Linux v6.12 RXE和锁定ns-3源码位置、摘要与原文hash。Linux实现仅支持协议语义核查，不是现代GPU/NIC校准。
- [archive.json](archive.json)：本地检查点约196KiB，245份原始日志、公开输入/输出、来源及实现快照逐项hash核对；不含二进制、账户数据或真实路由。仓库不跟踪归档本体，另行备份仍由使用者按原存储策略处理。

独立内存核心检查覆盖456项断言，包括所有两段切分、连续两帧的80种固定分段、权限/地址/对齐/冲突重复/序号跳跃、模2⁶⁴加法和共享目标不同上下文；重复fetch-add返回第一次的旧值且只修改一次。这是无时间核心验收，没有通过丢包触发网络重试。实际网络流程独立核对所有请求与回复的实际字节、每次目标应用、队列额度及关闭条件，不以程序返回0代替这些检查。

默认work1在work0目标应用后就绪，但深度1使其等待work0请求ACK；work3在原子work1请求ACK后就绪，又等待原子旧值到达才发布。work1的ACK/返回为24,130/24,146ns，work3在后者发布。所有原子返回0/7/10/9（按应用id2/1/4/5），最终目标值10；普通写入只改变指定偏移。末次应用完成后还等待反向流ACK，物理排空时刻66,394ns。数值只支持本人工配置下功能关系，不支持生产时延或速度提升。

应用id统一+100并同步依赖后，全部线上有效载荷、源包、网络时刻和最终内存保持相同；线上的匹配序号由上下文实际提交顺序产生。相同输入两次完整result逐字节一致。8条公开CLI均在新输出路径执行；前向依赖在输出创建前拒绝，错误NIC由native拒绝并保留child_failed。

## 复核

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_remote_transaction_workflow.py \
  --backend /ABS/PATH/ns3.42-moe-rdma-transaction-probe --output /ABS/NEW/check
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p 'scaleout_transactions_test.py'
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ibackend \
  tests/unit/remote_memory_test.cc -o /ABS/NEW/memory-test
/ABS/NEW/memory-test
```

这些命令应在外层使用仓库host_probe的60秒、RSS1GiB、地址2GiB及磁盘预算；运行目录中的命令和日志记录本次实际做法。没有执行全模型、千卡现代推理或硬件校准。

## 尚未支持

MRT1没有RoCE BTH/AtomicETH/AETH/AtomicAckETH/ICRC编码；请求ACK与反向数据回复是两个机制，实际RoCE原子ACK不能如此等价计费。原子应用没有NIC服务时间、max_rd_atomic/CQ容量或GPU内存序；窗口不支持注册成本、复用epoch、部分DMA可见或源buffer寿命；无lost-response RTO、PSN级应答缓存生命周期、真实flush或跨NIC强signal可见性。旧现代事件核心没有接入此入口。不得用这个模型给现代GIN排序、宣称现代基线已经建成或得出论文优化收益。
