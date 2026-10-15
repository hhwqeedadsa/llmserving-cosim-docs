# UB 分层输入与真实执行基线

此处验证单平面、单 spine 的 UB 树形功能；不是非 UB Scale-Out 或真实 NIC/RoCE 的校准。参考输入为 [examples/scaleout](../../examples/scaleout/README.md)，生成入口 `moe-ub scaleout prepare`。

## 独立检查与实际结果

2026-10-15，以主仓起点 `dd7d8e8`、现有锁定依赖及本任务新增平台代码验证：

- `fabric_test native_plan_test cann_plan_test model_capacity_test scaleout_test`，共 26 项通过。新检查包含独立 BFS、物理出口、三层速率经过 resolver 后不丢失、映射只改两个字段、非法输入/预算/旧目录和重复生成逐字节一致。
- `tests/integration/check_scaleout.py` 使用公开 prepare→resolve→run→validate；将示例活跃设备映射改为 `[0,1,4]`，前两者在同服务器的不同芯片，确实共享物理出口，第三者跨机架。
- 仅将服务器上联从 100Gbps 改为 10Gbps，完成时刻为 117098ns 与 146942ns。计划传输一致，所有 COMP 节点持续时间相同，至少一个消费者计算开始时刻改变。
- 两条件均检查唯一网络初始化、实际有效链路速率、每事务恰好一次 send/receive 回调、正常服务结束、网络排空以及服务器/机架实际观测到包。

结果只说明该固定 synthetic Decode 的容量与因果执行生效；不把 25.49% 的时间变化外推为硬件性能或网络机制创新。

## 复现与证据

按用户指南构建锁定后端后，在仓库根运行：

```bash
python tests/integration/check_scaleout.py --backend build/default/output/cosim/ns3.44-cosim-service --output-dir runs/scaleout-check
```

集成驱动及示例各自约束执行时间、内存、累计产物和磁盘预留；完整运行宜再以公共 `moe_ub_cosim.host_probe` 约束进程树。该次实际使用 wall 300s、RSS 2GiB、单进程地址空间 4GiB、产物 256MiB、磁盘预留 8GiB，不自动加限额。

日志根为 `runs/scaleout_validation_20261015/`：依赖、工具链、新构建、unit-probe，以及成功 `integration-02/summary.json` 和 `integration-probe-02/probe.json`。第一次集成输入因 observation 的单端口窗口上限超过总窗口上限而被拒绝；失败保留在 `integration/`，修正配置且新增预算检查后通过。系统 Python 缺 protoc 的初次回归失败记录也保留；通过结果来自有完整工具链的环境。

后续非 UB 路线见[源码研究](../../docs/reviews/scaleout_ns3_backends_20261015.md)，本 fixture 不承担它的验收。
