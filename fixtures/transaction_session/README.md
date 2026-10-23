# 在线事务会话验收

目标是让前端在同一原生ns-3网络中，根据真实完成继续递交工作，而不是启动时把未来所有工作交给后端。接口与可复制驱动见[使用说明](../../examples/scaleout_rdma_transactions/README.md#在线追加工作与实际回调)，实施范围见[规范](../../docs/tasks/transaction_session_spec.md)。本轮只实施平台，不做研究收益实验。

完整验收命令为：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_transaction_session_workflow.py \
  --backend /ABS/ns3.42-moe-rdma-transaction-session \
  --reference-backend /ABS/PRE_CHANGE_TRANSACTION_PROBE --output /dev/shm/NEW_SESSION_CHECK
```

此脚本应放在公开host_probe内，60秒/RSS1GiB/地址2GiB/产物64MiB、仿真5ms/结果16MiB及tmpfs8GiB预留。它从新cwd通过公开命令生成8GPU拓扑，不导入平台执行内部实现；评价重用独立标准库MRT1解码/内存重放检查。

[最终验收](verification.json)证明：

- 同一11节点程序（7远端、4本地）一次性递交，除了新增session字段，包、内存、队列和所有完成字段与修改前probe完全相等。前端刻意暂停20ms不改变仿真完成时间688,408ns；这是功能时钟不变量，不是主机执行零成本或加速比。
- 默认因果前端只在真实after事件发生时注册后继；核对48个实际状态、8个端点应用帧与32个源包。接收、请求ACK、原子回复、本地处理与完成分别检查。
- 两个动态条件只修改初始原子窗口7→19，实际回复7→19，随后才生成的新put内容107→119；逐包解码和最终目标内存均一致，原子值另变为10/22。请求QP只创建一次且复用两次，后继没有放在后端初始输入或第一批提交中。
- Python提交非法bool原子值被拒绝后，同一待决策仍可合法提交。绕过Python的11种错误（批内非法、时间/序号、过早结束、EOF、bool、重复ID、未来依赖、超容量、空闲空命令和超长输入）全部拒绝，且没有执行同批合法前部。
- 真实5ms截止保留未完成copy的输入/输出使用记录及captured快照，不补完成、不释放源，也未发送其远端后继。

[受影响回归](regressions.json)包含37条旧本地I/O/源窗口/事务命令、15项事务输入单元、7项RDMA输入单元和13项固定依赖检查。[构建](build.json)保存最终两入口身份；7个受监控构建阶段包括一次已保留的编译失败，不表示每阶段都通过。使用已有固定库和一致Fabric对象隔离增量构建，旧4二进制与8原生库保持不变；没有重新全量构建依赖。

失败保留在原件中：JSON积分类型转换歧义修为显式get；临时恢复构建脚本曾发生SyntaxError，未启动编译；首次会话错误地在结束前要求Fabric删除所有QP，导致空闲后不能继续/结束。最终使用事务传输的实际物理排空判断保留QP，在显式Close后再验证Fabric资源清空，没有放宽真实包排空或改限额。

`checkpoint.py archive/recover/final --repo /ABS/REPO --root /ABS/THIS_RUN`保存原件/源码/固定原生运行库，逐hash检查后换源码根和cwd实际执行公开最小例。摘要见[归档](archive.json)、[恢复](recovery.json)与[收尾](closeout.json)。整个阶段480秒/192MiB，归档成员96MiB/压缩32MiB；根卷不足8GiB仅存源码与这些小摘要，大件放`/dev/shm/moe-transaction-session-20261023/`，仍是易失存储，没有持久/远端备份。运行库恢复依赖宿主Linux/Python/glibc/libstdc++，不是完整离线依赖构建。

这一步移除了前端与原生事务之间缺少在线反馈接口的障碍。现代DeepEP runtime、真实张量与专家数值、self-peer服务、GPU可见性与匹配硬件校准仍未接通，不能以人工功能程序代替完整现代推理，更不能把本轮接口或测试数量当作A类论文贡献。论文目标继续进行中。
