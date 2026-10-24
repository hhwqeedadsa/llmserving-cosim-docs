# 事务准备事件与副作用顺序验收

本阶段从 `9038e44` 修复输入接纳缺陷：准备事件经过本地等待后，被错误当作跨连接重叠写的完成证明。实际网络反例、修复和限制见[报告](../../docs/reviews/网络丢包与跨连接旧写覆盖_20261024.md)，规则与预算见[规范](../../docs/tasks/transaction_effect_order_spec.md)。这是本仿真器的正确性修复，不是生产通信库漏洞或论文新算法。

`run.py`只使用公开CLI，保存改前竞争程序和两个安全对照。`--phase before`必须使用改前 Python 校验与改前二进制；不能用改后源码冒充改前环境。v5程序通过真实丢包、80μs恢复和两个本地声明时长形成受控交错，目标端复制真实收到的字节。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 fixtures/transaction_effect_order/run.py \
  --phase after --backend /ABS/ns3.42-moe-rdma-transaction-probe --output /dev/shm/NEW_EFFECT_ORDER
```

普通部署使用公开`rdma-build`构建入口。`build.py`仅复用本机固定编译收据进行本阶段隔离验收，不是用户安装依赖。`edges.py --runtime RUNTIME_ROOT`为本阶段测试：原生v1—v5拒绝、两个合法网络、已接纳旧请求后拒绝整个不安全在线追加批次。它不是正式研究驱动。

`audit.py`只用标准库，从源载荷和端点捕获独立解码MRT1、按接收端连续前缀重放实际接纳、按真实目标次序重放内存及复制，覆盖7个条件、38个源包/端点TX见证。两组安全结果改前改后完整字节一致；伪造复制内容、写入时间和通知值均拒绝。

```bash
python3 -B fixtures/transaction_effect_order/audit.py \
  --runtime /ABS/RECOVERY_RUNTIME_ROOT --output /ABS/NEW_AUDIT.json
```

首次独立检查器把最早物理RX当成已交付字节，没有排除乱序时被RC丢弃的包，因此在同连接对照的应用时间检查失败。保留`audit-initial.py`和失败说明，修复为独立连续前缀接纳后复算原结果；没有重跑网络来迁就检查器。

25项相关Python单元、8条原事务公开命令、3条在线公开命令及其直接交互、32条前缀工作流命令（含预期拒绝）通过；后者包含新旧默认完整结果一致。13项固定依赖一致，隔离构建的旧二进制/库身份保持。不能把预期拒绝写成全部命令exit0。

`checkpoint.py --runtime RUNTIME_ROOT --stage pack|restore`分步保存/核验有界检查点。包内包含改前Python校验、改后源码、实际新旧入口、8个原生库和主要输入/结果；常规回归只收摘要，原件仍在原运行目录。解包后可在新cwd离线复算audit，并使用包内对应Python/二进制和`LD_LIBRARY_PATH`重放网络；具体已执行命令保存在恢复收据。仍依赖宿主Python、loader、glibc和C++系统库，不是依赖源码离线构建。

原运行目录为`/dev/shm/moe-effect-order-20261024/`，易失；持久本地包在`runs/transaction_effect_order_checkpoint_20261024/`。归档hash、重放和预算实值见[恢复](recovery.json)与[收尾](verification.json)。本地副本仍在同一存储上，不是远程备份。源/目标GPU可见性、现代provider撤权或跨代际恢复均未由本验收证明。
