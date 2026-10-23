# 实际接收窗口转发与读保护验收

transactions/3的put可从当前注册源窗口读取实际字节，将网络接收内容作为后续通信输入。源范围从提交到MRT1事务完成保持读使用记录，发布时快照；它是功能模型，没有GPU逐包读取或GIN/CQ成本。输入和普通运行方式见[公开示例](../../examples/scaleout_rdma_transactions/README.md#将实际接收内容作为后续发送的数据)。

## 复核方法

在仓库根执行，两个程序分别为新构建和保留的v2参考版本，输出目录不能存在，父目录至少预留8GiB：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/integration/check_source_window_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-transaction-probe \
  --reference-backend /absolute/v2/ns3.42-moe-rdma-transaction-probe \
  --output /absolute/new-output-directory
```

新cwd中的公开CLI从版本化示例生成输入，不从旧运行目录读取任务。独立struct解码器按源包序号还原MRT1帧，再用目标应用、本地消费、source acquire/capture/release的共同order独立重放窗口和活跃读。capture值须等于当时实际内存且与包体相同，不能只比较报告中的两份hex。前缀检查支持失败时尚未完整发出的帧，只应用实际目标已解码的请求。

回复匹配和端点ACK核对依赖本固定人工例的唯一时刻/端点/前缀，检查器显式断言这些条件；不是一般多流报文关联器。每命令60秒/RSS1GiB/地址2GiB/阶段64MiB，仿真5ms/结果16MiB，累计480秒/192MiB，归档成员128MiB/压缩48MiB，预留8GiB；失败不抬限重跑。

## 实际结果

- 29条公开CLI、8条原生检查；人工例6个远端事务、3个声明本地阶段，4次源窗口put、27个源数据包、1,525B载体负载，最终无活跃读记录。
- 只改初始窗口第8—256字节，operations完全相同，4次实际源快照和转发包均改变。重复结果逐字节一致，operation id改名不改变线上包或时刻。
- 两次同源读同时提交，第二次因queue_depth=1排队；排队也保持读保护。完成后合法覆写继续执行。
- 远端put、原子和本地reset三种冲突均在写入前拒绝，保存真实部分结果和两个活跃读。本地多个reset先全部检查，没有先改外部计数再失败的部分消费；被拒绝本地节点没有标记为已入队。
- 提交前覆写对照：GPU4本地处理期间、后继put提交之前合法修改接收窗口，后续转发采用新值。这验证发布时读取，也明确暴露尚未保护本地处理输入的缺项，不能解释为应用数值正确。
- 少计数保留4个下游远端事务未提交；1Mbps链路/5ms截止保留源读记录且没有目标应用，不伪造释放或完成。
- work/local-waits/queue-fences/local-stages四组新旧结果逐字节一致；纯字面量v3去掉3个新增事务字段等于v2。8类非法输入均经CLI与原生拒绝，无结果文件。
- 独立内存检查2,582项断言，包含全部2,080个非空子区间的字节交集枚举、重叠读、写/原子/reset、重复请求及释放复用；12项输入检查、4组受影响流程和13个固定依赖检查通过。

[主验证](verification.json)、[回归](regression.json)、[构建与资源](build.json)、[中文HTML](../../docs/review_artifacts/source_windows_20261023/report.html)、[归档](archive.json)、[恢复](replay.json)、[收尾](closeout.json)。初版流程36命令通过后新增提交前覆写对照，最终37命令；末尾补Drained必须无残留lease并增量编译、重新链接后完成最终回归。早期构建、结果及旧二进制保留。一次文档补丁因目标标题不匹配未应用，修正后写入，不涉及网络执行失败。

## 保存和研究边界

原始输入、命令/cwd/退出状态、源码快照、日志及资源监测位于`/dev/shm/moe-source-windows-20261023/`，仅易失内存盘，**无持久备份**。恢复验证检查包内源码、新旧二进制及8个原生共享库；仍需要宿主loader/glibc/libstdc++，没有完整依赖离线源码构建或跨系统镜像保证。版本化JSON保留验收摘要，不包含全部原始包。

本地处理无数值输入输出和输入lease；源保护是MRT1请求ACK/按序完成的功能选择。全局applied依赖不代表源GPU可知状态。现代DeepEP在线事件/provider、self-peer物理服务、GPU可见与源安全、硬件校准和完整千卡执行仍缺失。没有新数据下载、依赖修改、GPU执行或优化收益结论，论文主贡献未成立，研究目标继续进行。
