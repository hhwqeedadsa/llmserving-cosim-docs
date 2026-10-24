# 源NIC限速后的唤醒正确性

原生Scale-Out库已修复源NIC调度的两个边界：已发完未ACK、被窗口阻塞或被PFC暂停的QP，不再用旧`m_nextAvail`挡住其他QP；新增更早工作可以提前原有唤醒。变速前后的资格检查采用更新后的有效窗口。没有改变ACK优先级、数据RR、实际限速或物理端口串行服务，没有加入忙轮询和新的应用通信时刻。

## 已核对的机制

使用两个原生NIC和一条100G、单向10us传播的链路，连接A发送1,000B、连接B发送16,000B，初始速率分别100G和10G。所有数据和ACK均由原生RdmaHw生成并经真实channel传输。被动记录器只读取选择时的QP及定时器状态，不注入事件。

A发送完后仍等实际ACK。B首包83ns发送，物理发送在166ns结束，下一包实际限速截止912ns。旧库此时未安排唤醒，直到20,087ns收到A的真实ACK才继续；修复库在912ns自动发送，早于任何ACK到达。两个条件各17数据包/17 ACK、总payload17,000B，物理配对、完整字节、反馈和端口串行服务独立核对。

B最终ACK完成51,780→32,605ns，差值19,175ns来自这个特定功能用例。该结果不是MoE完整应用收益或硬件测量，不能直接解释旧研究的所有空闲，也不是新的恢复算法。

另有11个原生CPU调度条件、2个真实ChangeRate的变量窗口条件和2个同纳秒暂停/唤醒顺序条件，使用人工QP状态，在选到可发送QP前停止，不发任何数据/控制包。旧11条件中5个错过应有唤醒；修复后全部满足，全部不可发时也不保留无用定时器。速率推后可以保留早到回调重新检查，不将一次额外回调误称为提前发送。

公开v5事务入口启用选择重传与16us NACK等待，首个请求包实际丢失一次；5次远端操作、3次本地copy和25内存事件由实际收到的MRT1字节驱动并独立核对，目标副作用恰好一次。平台不借此声称现代GIN或生产恢复语义。

## 输入、命令和验收入口

[事前规范](../../docs/tasks/nic_wakeup_spec.md)记录范围和900秒/512MiB、最多4网络的预算。`inputs/`是公开入口的小型配置，不需要模型下载或原始路由数据。普通用户可在完成原生构建后运行：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout topology \
  --config fixtures/nic_wakeup/inputs/topology.json --output /tmp/nic-topology
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /tmp/nic-topology --protocol fixtures/nic_wakeup/inputs/healthy-protocol.json \
  --config fixtures/nic_wakeup/inputs/healthy-work.json --backend /path/to/moe-rdma-stream-probe \
  --output /tmp/nic-healthy
```

输入包含声明宿主限额；输出路径须尚不存在。`tests/nic_wakeup_native.cc`、`tests/nic_wakeup_rate_native.cc`、`tests/nic_wakeup_same_time.cc`为CPU状态核查，`tests/nic_wakeup_packets.cc`为实际原生包级复现。`verify.py`独立读取全部保存的原件，不导入平台传输解码器或调用网络。

原生补丁只在本地保存到native→ASTRA→LLMServingSim→父仓库的提交链，未push，不声称新提交可从远端取回。归档保留源补丁/本地Git来源，以及固定程序/库身份；普通重建仍通过公开`scaleout rdma-build`从固定源码进行。本机历史运行目录不是远程备份。

四次网络已经关闭；新源码根/空cwd再生成拓扑并实际执行默认持久小例，全部八库从恢复根加载。最终两份离线核验逐字节一致，见[独立核验](verification.json)、[恢复](recovery.json)、[资源收尾](closeout.json)与[中文报告](../../docs/reviews/源NIC遗漏唤醒的包级复现与修复_20261024.md)。持久包为本机`runs/nic_wakeup_checkpoint_20261024/evidence.tar.gz`，包含同ABI运行时、源码和证据。解包只校验/写入文件，不自动运行网络：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.nic_wakeup.checkpoint restore \
  --archive runs/nic_wakeup_checkpoint_20261024/evidence.tar.gz --output /tmp/nic-wakeup-review
```

目标目录须尚不存在；成员全部校验后才写入，解包应纳入宿主磁盘/时间预算。`runtime/`不是干净构建产物；ABI及系统依赖仍有约束，实际运行命令和动态加载参数保存在包内。此前研究归档不覆盖、不重跑；受影响的完整图比较须在本平台独立验收提交后另立研究协议。整体论文目标仍进行中。
