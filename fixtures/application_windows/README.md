# 应用窗口引用与关闭验收

基线`8f0869a`，要求见[规范](../../docs/tasks/application_window_spec.md)，公开流程见[示例](../../examples/scaleout_rdma_transactions/README.md#应用窗口关闭与底层注册分开)。本增量把已核清的共享底层引用和本地使用寿命接入实际事务；不是动态MR撤销或恢复优化。

8GPU、14交换机、两plane；沿用固定原生ns-3及四项静态NIC注册。三个应用视图引用两个物理窗口。目标先关闭视图10，经视图11复制8B，再关闭视图11；源经自己的视图1发出8B，实际ACK完成后关闭源视图。任务依赖用于人工反例编排，不是发送方读取目标内部关闭状态。

| 实际检查 | 结果 |
|---|---|
| v4、v5稀疏、v5前缀、在线session | 各1远端+4本地完成，终点12,460ns；生命周期7事件 |
| 目标最后应用引用 | 300ns归零，状态仅为provider待处理 |
| 真实网络 | 1源包/40B载体有效负载（32B MRT1头+8B数据），6,336ns写目标，12,360ns源ACK完成 |
| 共享视图 | 关闭10后，11继续复制实际初值；最终网络写和此前复制均逐字节保留 |
| 默认关闭新功能 | 新/旧NIC示例完整result逐字节一致 |
| 非法配置 | CLI/native各18条件在建网/分配输出前拒绝 |
| 在线候选 | Python拒绝整批后保持config/pending/sequence/控制流并继续完成；原生整批拒绝后没有执行其中任何成员 |
| 核心与回归 | 59项不同Python单元、6,668项C++视图断言；13项固定依赖检查通过 |

这些ns值是人工MRT1功能例，不是硬件性能、MoE收益或生产故障率。最后引用释放后，实际NIC/rkey继续授权数据包是本模式定义的边界，不能将它写成“真实注销不安全”。

`audit.py`只用标准库，独立解析实际包和ACK、复制初值、重放最终字节，按实际操作完成核算引用和使用；仅支持本组有严格因果链的无丢包单帧put和前置copy。关闭时刻、引用数、线上字节、最终内存、provider主张五种篡改均拒绝。`online.py`是两次小型功能会话（健康继续及预期原生拒绝），只依赖公开session接口和原生命令，不是实验方法。

```bash
python3 -B fixtures/application_windows/prepare_inputs.py \
  --work examples/scaleout_rdma_transactions/application-windows.json \
  --output runs/application-window-variants
PYTHONPATH=python python3 -B fixtures/application_windows/reject_inputs.py \
  --topology runs/nic-topology \
  --protocol examples/scaleout_rdma_transactions/nic-registration-rdma.json \
  --work examples/scaleout_rdma_transactions/application-windows.json \
  --backend build/native-transactions/output/moe-rdma/ns3.42-moe-rdma-transaction-probe \
  --output runs/application-window-rejections
```

将普通probe配置换成生成的sparse/explicit.json可复核v5；session使用相应session程序。`online.py`参数与reject_inputs相同，backend应换为session二进制，另给新输出目录。真实源码根/空cwd恢复、累计预算及持久包在`recovery.json`、`closeout.json`；功能摘要在`verification.json`。不依赖这些历史产物也能执行示例。

当前仅声明式本地应用引用。真实底层注册复用协商、缓存引用、MR分段/逐连接退出、provider失败未知、DMA、重新注册/发布及成本仍未实现；正常provider强退出、协同排空、独立底层分配和备用窗口必须继续保留为研究强基线。未证明合法故障并发、优化收益、校准、代表性负载或A类论文贡献。
