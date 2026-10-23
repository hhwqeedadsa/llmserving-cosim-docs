# 有界稀疏窗口验收

本轮补齐的是现代布局地址范围的功能表示：同owner/key下保留完整逻辑范围，只为写过的4KiB页存实际字节，不再要求用64KiB完整hex覆盖整个窗口。公开配置和读回格式见[使用说明](../../examples/scaleout_rdma_transactions/README.md#使用大地址范围与稀疏初值)，实施约束见[规范](../../docs/tasks/transaction_sparse_windows_spec.md)。平台独立验收，不做研究收益实验。

[原生验收](verification.json)包括6条公开命令及16条直接原生拒绝：

- 同一11节点输入改为v5稀疏初始化后，仅将窗口结果还原为原hex，其他全部包、内存事件、队列和完成字段与v4完全相等。
- 三个18MiB逻辑窗口合计56,623,104B，终态保存61,440B页字节；另有容器、索引、事务及ns-3状态，不是总RSS或GPU显存。实际发送两个14,464B put及一个原子请求/回复，共456个源包；这里只计源数据/返回包，另有ACK及多跳发送。
- 18MiB长度、staging偏移和14,464B长度与公开modern EP4/H7168/Top8布局核对。人工数据在2^32基址上实际接收→零声明时间的受保护copy→后继源put；只改最初一个字节，实际copy及两个源put的内容均变化。默认在线前端与一次性程序除了session字段，完整result一致。
- 两个原生入口分别拒绝bool/超长窗口、未知初始化、重叠/越界/过多初值段、过多初始字节及64位地址溢出，均在执行网络前失败且不产生结果。记录中的16次是8类×2入口，不是16类独立机制。

[独立核心检查与回归](regressions.json)保留6,483项稀疏字节字典/边界断言和6,758项既有核心断言，16项输入单元、37条本地I/O回归、在线会话回归及v1/v2四条公开命令差分。页上限两页写、原子新页和本地copy失败检查整个写前后页内容不变，远端失败后同序号可合法重试，本地输出记录保留；这不是网络丢包重试机制。现有v1/v2/v3与原入口逐字节一致，v4也保持旧字段；新源窗口/排队copy冲突与真实截止由旧回归继续检查。三种评价篡改的拒绝记录在[独立核验](oracle-mutations.json)。

[构建](build.json)采用固定原生库及已有编译/链接命令隔离构建两个事务入口和两个核心检查，共9个受监控阶段，旧15份运行文件hash不变；没有全量重建依赖。固定13项依赖另检查。全部采用每命令60秒、RSS1GiB/地址2GiB、阶段64MiB、仿真5ms/结果16MiB；全阶段480秒/192MiB、tmpfs预留8GiB，没有自动增限、安装下载、GPU执行或旧失败Prefill/1024重跑。

完整功能检查可在独立host_probe内运行：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_sparse_windows_workflow.py \
  --backend /ABS/NEW_TRANSACTION_PROBE --session-backend /ABS/NEW_TRANSACTION_SESSION \
  --output /dev/shm/NEW_SPARSE_CHECK
```

`checkpoint.py archive/recover/final --repo /ABS/REPO --root /ABS/THIS_RUN`保存有界原件、源码、两入口与8个固定库，恢复到新源码根/cwd后，通过3条公开命令执行2次小网络并独立重放字节。使用host_probe和上述预算，归档成员≤96MiB/压缩≤32MiB。摘要见[归档](archive.json)、[恢复](recovery.json)与[收尾](closeout.json)。根卷不足8GiB仅保存源码/小摘要，大件在`/dev/shm/moe-sparse-windows-20261023/`，易失且没有持久备份。恢复仍依赖宿主Linux/Python/loader/glibc/libstdc++，不是完整离线依赖构建。

这不是现代DeepEP完整网络接入：self-peer仍不支持，实际GPU目标可见性、真实张量与专家数值、provider成本和匹配校准仍缺。4KiB页、零初始化和驻留上限是功能存储选择，不是GPU分页或内存性能模型。本轮没有新的策略性能收益或A类论文主贡献，整体研究目标继续进行中。
