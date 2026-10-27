# 独立RC操作状态参考

用于检查后续网络后端是否保留WRITE、原子返回值和源完成的区别。它运行一个有限、无时钟的状态模型，不连接ns-3，不模拟NIC注入、拥塞、超时或GPU内存序。

在仓库根目录运行：

```bash
PYTHONPATH=python python3 -B examples/rc_operation_reference/run.py --output /tmp/rc-example.json
```

输出路径必须尚不存在。小例通过公开的`moe_ub_cosim.rc_reference`接口构造一个600B写入和一次fetch-add；健康与丢反馈两种情况都只将目标从41增加到46，并最终返回旧值41。丢反馈后，重复WRITE产生的普通ACK不能替代原子返回值，源保护与原子额度保持到实际Atomic ACK。后续重传由调用者显式交付，没有自动计时或恢复策略。

可复用接口为`RcRequester.post/send/receive/retransmit/snapshot`及`RcResponder.receive`；`Request`、`Response`是类型化的操作包。请求包仅带PSN、操作码、合法分支需要的远端字段和数据，没有任务ID、request ID或MRT1包头。返回响应不再作为另一条可靠数据流发送，也不生成对响应的额外ACK。

| 项目 | 当前范围 |
|---|---|
| 连接 | 一个已建立QP，固定单个注册区/rkey；单线程 |
| WRITE | 非空；FIRST/MIDDLE/LAST/ONLY；逐包目标写入可见 |
| 原子 | 对齐的64位fetch-add，显式小端目标内存，模2^64加法 |
| 确认 | WRITE按固定RXE的END或`noack_pkts++ > 64`请求ACK；原子返回Atomic ACK |
| 资源 | 最多64项操作、累计64KiB写入；原子额度与有限FIFO重放缓存各1～64 |
| 分包 | 显式256/512/1024/2048/4096B载荷MTU；每次send返回一项完整WQE的无时序包组 |
| 完成 | WRITE可由累计ACK完成；原子必须收到旧值；源保护在完成后释放 |
| 不支持 | PSN回绕、零长度WRITE、READ、SEND/RNR、完整QP错误和恢复状态、NIC包窗口/门铃/调速、网络/硬件耗时 |

调用者必须使原子发送额度不超过目标重放资源；本参考不协商连接参数。更晚PSN的ACK越过未完成原子时返回`retry_required`，不会猜出返回值；乱序请求返回一次序号NAK后抑制重复，接受缺失前缀才恢复。重放项被覆盖后的旧原子请求会丢弃，不再次加法。非法注册范围/权限或不支持状态抛出明确异常，不实现错误QP继续运行。

安全策略名为`atomic_return_required/1`。它与所核Linux v6.12 RXE有一处已知分支差异：普通ACK恰好落在待完成原子PSN时，RXE所提取完成函数可释放操作而没有写回旧值；本参考拒绝这种推定。原函数只在替代环境中执行，未在完整内核或NIC验证，**不称RXE逐状态等价实现或已确认生产漏洞**。固定来源、原分支对照和复核范围见[验收](../../fixtures/rc_operation_reference/README.md)。

本参考尚未接入现有事务或现代MoE入口，旧MRT1与旧结果保持各自原语义。它为后续操作级网络接入提供验收依据，不产生新的MoE加速、仿真加速或两项创新成立的结论。
