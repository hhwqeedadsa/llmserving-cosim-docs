# 原生RDMA软件准入与资源语义验收

公开入口检查：[check_rdma_admission.py](../../tests/integration/check_rdma_admission.py)，不依赖历史运行、外部数据或测试网络替身。

```bash
python tests/integration/check_rdma_admission.py --backend-json <原生构建>/backend.json \
  --protoc <既有protoc> --output <不存在的目录>
```

本轮额外提供修改前保存的`--previous-probe`、`--previous-service`，对照同一锁定ns-3库上的旧可执行，确认默认结果逐字段相同。未来复现可从本轮父提交单独构建旧版本，不能将新二进制复制后称为旧版本。

完成11个探针与9次实际LLMServingSim服务运行，覆盖默认/透传/仅观测/截断等价、FIFO插入顺序、容量2全占用集合、目的端轮转、显式决策成本、最终ACK停滞、独立源推进、同机crossbar排除、双plane共用额度及同纳秒ACK。预定义故障须非零退出且保留结果。失败服务诊断区分已提交但待额度与已发出后待ACK。

双plane最小探针曾暴露审计缺陷：QP映射在原生ACK回调返回后同步移除，软件额度在ScheduleNow事件中归还；后续同纳秒ACK可在两者之间发生。修复的是离线核对顺序，原生传输成功结果没有改变；未删减容量、计数或释放断言。锁定`RdmaHw::QpComplete`及其Noop应用通知是此重放规则的源码前提。

另完成原丢包11探针/3服务回归、51项Scale-Out Python单元检查、纯C++所有权/顺序单元检查、13个依赖检查，以及公开`fifo2.json`的完整小型服务流程。未重新验证完整Scale-Up网络，没有硬件校准或策略收益结论。

实际运行、源码摘要及检查入口见[evidence.json](evidence.json)。本机持久原始归档为`runs/scaleout_admission_20261021/evidence.tar.gz`，同目录`archive.json`含逐文件清单和校验值。软件限额不是NIC硬件容量，发送映射移除不是所有协议对象/GPU缓冲释放。独立研究另存`experiments/scaleout_admission/`，不将功能门作为研究收益证据。
