# Scale-Out 超时恢复与失败诊断

这个例子解决锁定原生 RDMA 的一个限制：最后一个数据包或最后一个 ACK 丢失后，没有后续包触发 NACK，发送可能一直无法完成。显式开启 `go_back_n` 后，发送端按真实 ACK 进度计时，超时从最早未确认字节重新发送；接收端遇到完整重复包时重新发送累计 ACK。重传与 ACK 都经过实际网络，保留原生选路、排队、PFC、DCQCN 和物理带宽。

需要可选RTT估计与源额度阻塞证据时，另见[自适应示例](../scaleout_rdma_adaptive/README.md)。本例继续使用固定超时。

默认不启用恢复。它是本项目声明的字节序号 Go-Back-N 扩展，尚未通过生产网卡测量校准；不代表完整 RoCE 可靠连接实现。机内 crossbar 不使用这套协议。预算沿用 [RDMA 小例](../scaleout_rdma/README.md)，不自动扩大。

## 运行一个最小对照

从主仓使用项目 Python 环境，设置 `PYTHONPATH=python`。先按 RDMA 小例构建当前源码，从构建目录 `backend.json` 读取可执行文件。以下 `$COSIM_PROBE`、`$COSIM_SERVICE` 和 `$COSIM_PROTOC` 分别是该构建的 `probe`、`service` 和已有 `protoc` 路径；输出目录必须不存在，卷保留至少 8 GiB。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/recovery-topology
python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/recovery-topology --config examples/scaleout_rdma_recovery/recover-final-ack.json \
  --messages examples/scaleout_rdma_recovery/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/recovery-ack
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/recovery-ack --output /dev/shm/recovery-ack-diagnosis.json
```

GPU 0→4 发送 4000 B。最终 ACK 被丢弃一次时，接收数据已齐；一次超时重传后，真实反向 ACK 使发送完成一次、额度归还一次。`source_data_payload_bytes` 包含重传，大于业务量；唯一字节仍为 4000 B。

用新输出目录把配置换为 `native-final-ack.json`，预期仿真非零退出、5 ms 内发送未完成。换为 `retry-exhausted.json`，预期两次超时重传后仍收不到确认，以 `recovery_retry_exhausted` 失败。两种失败都可诊断，诊断命令成功不表示原仿真成功。`recover-clean.json` 提供无故障恢复基线。

## 用真实前端执行

```bash
python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_rdma/service.json --topology /dev/shm/recovery-topology \
  --protocol examples/scaleout_rdma_recovery/service-final-ack.json \
  --backend "$COSIM_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/recovery-service
python -m moe_ub_cosim scaleout rdma-check --run /dev/shm/recovery-service
```

目标是原小例消息 0 的 2048 B 最终 ACK。服务配置把详细包记录限制为 1000 条，避免大量包事件超过结果 JSON 的 16 MiB 读取预算；截断显式标记，协议计数与恢复记录继续累计。改模型、batch、EP 组或路由后，要先从无故障运行的 `graphs/*/wave.json` 选择实际目标，再改故障规则。完成反馈继续推动 ASTRA 图与 LLMServingSim 下一轮组批，没有预设后续发送时刻。

## 修改恢复参数

在传给 `rdma-probe --config` 或 `rdma-run --protocol` 的完整 RDMA JSON 中增加：

```json
"recovery": {
  "mode": "go_back_n",
  "initial_rto_ns": 40000,
  "max_rto_ns": 160000,
  "max_retries": 3,
  "max_events": 100000
}
```

| 字段 | 含义与约束 |
|---|---|
| `mode` | 缺省或仅 `{"mode":"native"}` 保留原生协议；`go_back_n` 时其余四项全部必填。 |
| `initial_rto_ns` | 首次发出实际数据包时启动的超时，单位 ns，1 至当前 `max_sim_ns`。等待准入不启动计时。 |
| `max_rto_ns` | 超时后倍增的上限，至少为初值，最多 1 秒。新的累计 ACK 进展恢复初值；重复/过期反馈不延后截止时间。无自适应 RTT 估计。 |
| `max_retries` | 连续没有 ACK 进展时最多 1—16 次超时重传决策，次数用尽后的下一次超时失败。原生 NACK 重传另外发生；决策次数不等于实际重传包数。 |
| `max_events` | 源事件与接收端重复 ACK 事件共享记录上限，0—100000。达到上限只截断记录，不改变协议；计数继续累计，完整性显式导出。 |
| `terminal_snapshot` | 可选布尔值，默认关闭；开启后在Report只读存活源QP的速率、下次允许发送时刻、窗口和发送/确认游标。已清理QP只报告不存在，不猜测最后速率。它不增加事件、不改变恢复决策，且只适用于此处的逐消息GBN。持久传输另有源端速率观测。 |

短超时会产生不必要的重传与反向 ACK。示例 40 μs 是功能验证的声明值，不能直接推广到千卡网络。

排查“多次触发恢复却没有发包”时，可在上述恢复对象中添加 `"terminal_snapshot": true`，再用相同公开probe或service命令生成新目录；成功和失败原件都保留。随后执行 `scaleout rdma-diagnose --run 新目录 --output 新诊断.json`，其 `recovery.terminal_snapshots`给出经过校验的终态观察。该诊断命令不会执行网络，诊断成功也不表示原任务成功。

每个快照包含 `time_ns/qp_present`；QP存活时还包含 `rate_bps/max_rate_bps/next_available_ns/last_packet_model_bytes/snd_nxt_bytes/snd_una_bytes/remaining_bytes/configured_window_bytes/effective_window_bytes/window_blocked/variable_window`。时间单位为ns，速率为bit/s，其余字节字段为B。`next_available_ns`可以早于或晚于采样时刻，只表示节奏门限，不包含PFC、链路和NIC仲裁的完整状态。只有终态，没有历史轨迹；不能据此推断此前所有超时都由限速造成。

`terminal-incast.json`固定一个不需要真实数据或历史结果的失败场景，配合公开生成器使用：

```bash
python examples/scaleout_switch_drops/prepare.py --output runs/terminal-inputs
python -m moe_ub_cosim scaleout topology \
  --config runs/terminal-inputs/topology.json --output runs/terminal-fabric
python -m moe_ub_cosim scaleout rdma-probe \
  --topology runs/terminal-fabric --config examples/scaleout_rdma_recovery/terminal-incast.json \
  --messages runs/terminal-inputs/messages.json --backend "$COSIM_PROBE" --output runs/terminal-incast
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run runs/terminal-incast --output runs/terminal-incast-diagnosis.json
```

其中probe预期返回1并保存重试耗尽；诊断返回0并保留原失败。7个源各发送262144B到一个目标，接入100G、上层25G、缓冲1MiB、分包9000B；不是完整推理或生产校准。每个native条件限60秒、RSS1GiB、地址2GiB、模拟5ms、产物64MiB、结果16MiB，保留8GiB磁盘空间。定向采集的[时序与验收](../../fixtures/recovery_terminal_snapshot/README.md)说明为什么失败早于下一次允许发送；不要将其当作优化收益。

## 如何判断结果可信

`result.json.recovery` 保存源端发送最高字节、累计 ACK、连续重试、超时重传总次数、当前 RTO、截止时间及终态。`events` 的列与编码随输出提供；`receiver_events` 记录接收端本地重复确认。完整记录才逐事件重放审计，截断记录只审计终态与计数。

发送完成来自原生完整 ACK 和 QP 清理；接收就绪不代替发送完成。服务全部图完成后还等待在途包、交换机队列、NIC ACK 队列和恢复计时器排空。原生拥塞控制的维护事件不属于待完成业务数据。

重试耗尽停止整个共享仿真，保留未完成通信、占用额度和业务图供诊断，直到进程退出统一销毁；不伪造成功、不继续执行剩余请求。`simulation_time_limit` 表示仿真预算先到，与协议重试耗尽分别报告。当前不支持单 QP 错误传播后继续其他业务、故障重路由或作业重启。

协议依据与功能覆盖见[模型依据](../../docs/model_validation.md)和[恢复验收](../../fixtures/scaleout_recovery/README.md)。
