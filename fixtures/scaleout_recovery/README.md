# 原生 RDMA 可选超时恢复验收

本组检查验证平台可靠性，不以策略性能收益为通过条件。原生依赖固定为 `3964b1d41cb5f7b51004c316b0eae1a3713c2498`，依赖源码与 gitlink 未修改，不混链 UB。

```bash
PYTHONPATH=python python tests/integration/check_rdma_recovery.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/recovery-gates
PYTHONPATH=python python -m unittest discover -s tests/unit -p 'scaleout*test.py'
g++ -std=c++17 -Wall -Wextra -Werror -Ibackend tests/unit/retransmission_timer_test.cc -o /dev/shm/recovery-timer-test
/dev/shm/recovery-timer-test
```

需要比较修改前二进制时，功能门另外接受 `--previous-probe`、`--previous-service`，须先保留原构建；普通新检出无须历史产物即可完成其余检查。构建与用户流程见[恢复示例](../../examples/scaleout_rdma_recovery/README.md)。

| 检查 | 实际结果 |
|---|---|
| 恢复功能门 | 19 次 probe、5 次实际服务通过预期断言。尾包、最终 ACK、NACK 丢失与连续丢失可恢复；短超时产生重复包仍只完成/释放一次。 |
| 失败语义 | 重试耗尽明确终止、保留 QP/额度/业务未完成状态；仿真预算先到单独报告。没有成功回调或释放造假。 |
| 边界 | 同刻 ACK/超时、双 plane 独立计时、其他源推进、待准入通信恢复后继续、机内 crossbar 不进入 RDMA 恢复。 |
| 网络排空 | 实际服务等待尾部重复包/ACK 排空；成功时恢复状态全部完成、在途为零、发送资源回收。 |
| 默认兼容与观测 | 实际旧二进制默认结果一致；清洁恢复模式除新增恢复报告外逐字段一致。完整/关闭/截断包记录不改变执行；恢复记录截断不冒充完整重放。 |
| 原功能回归 | 原故障 11 probe/3 服务、原准入 11 probe/9 服务、结构 11 probe/3 服务及执行前超额拒绝通过。 |
| 单元检查 | 60 项 Scale-Out Python 和计时器/准入/队列积分 3 个纯 C++ 检查通过。包含伪 ACK、倒退时间、旧 ACK、退避上限、错误终态和截断审计。 |

实际根目录 `/dev/shm/moe-recovery-20261022/`；[evidence.json](evidence.json)是原结果提取的有界摘要，详细命令、输入、成功/失败结果及资源记录在运行根目录，最终归档入口见当前任务。各门的 `commands.json` 逐条登记实际退出码，失败对照保留非零退出。共享内存随机器重启丢失。

公开流程另从新拓扑执行 3 个 probe 与完整 2 层4步服务。首次服务因 83388 条包记录使结果 17933537 B 超过 16 MiB 读取上限而失败；未扩大预算，改服务示例包记录上限为1000条后以新目录完成。原失败目录保留，不将网络 `passed` 当成用户流程成功。

本轮不跑千卡，不做硬件 RTO 校准，不实现自适应 RTT、故障重路由或失败 QP 后剩余作业继续。包重传决策数与实际包数分别记录。
