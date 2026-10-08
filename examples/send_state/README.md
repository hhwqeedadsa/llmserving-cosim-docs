# 端侧发送资源记录小例

本例为三个 rank（计算端点）的一轮合成解码开启实际发送资源记录。输入复用仓库内的原生 STORE/LOAD 小例，默认每 rank 两个发送槽；不需要历史运行、外部数据或模型下载。

完成[安装和构建](../../docs/user_guide.md)后，在仓库根目录执行，输出目录须不存在：

```bash
moe-ub resolve --config examples/send_state/scenario.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service --output runs/send-state-input
moe-ub run --config examples/send_state/run.json --output runs/send-state-run
moe-ub validate --observed runs/send-state-run/observed
moe-ub slice create --from-run runs/send-state-run --output runs/send-state-slice
moe-ub slice inspect --slice runs/send-state-slice --verify
moe-ub sample --slice runs/send-state-slice --config examples/send_state/sample.json \
  --output runs/send-state-sample
moe-ub replay --slice runs/send-state-slice --config examples/send_state/replay.json \
  --output runs/send-state-replay
moe-ub validate --run runs/send-state-replay
```

自选构建目录时，替换解析命令的 `--backend`，并给 `run`、`replay` 同样增加 `--backend`。更换输入输出位置时同步修改 `run.json.inputs`。配置路径相对配置文件，命令路径相对工作目录。示例宿主预算为单阶段 300 秒、4 GiB 驻留内存、8 GiB 地址空间、2 GiB 产物，保留 20 GiB 空闲；已有大量产物时，在各配置的 `host.artifact_roots` 中登记共享累计目录。

`observation.json` 使用 `moe-ub-observation-config/4`，其中 `send_state` 显式开启采集，并限制每 rank 和全局记录条数。删除该项并退回 `/3` 即关闭；其他统计保持原配置。`scenario.yaml` 的 `execution.send_slots_per_rank` 决定槽容量，修改后重新解析并运行。

新增表通过现有公开读取器访问：

```python
from moe_ub_cosim.research_storage import stored_rows

for row in stored_rows("runs/send-state-run/observed", "endpoint_send_state.jsonl"):
    print(row)
```

三项计数分别是已经就绪但尚未提交的 SEND 数、实际占用发送槽的 SEND 数、配置的槽容量。它们属于整个 rank，不表示某个操作或专家的独占状态。`ready_send_count > 0` 且 `active_send_count == send_slot_capacity` 表示该次调度扫描后存在待发任务且发送槽已满；这本身不能区分下游网络、协议完成或其他原因。

同一个 rank 按 `sequence` 阅读，时间相同时保留顺序。状态保持到下一条记录；`final` 是采集范围结束边界，可能晚于实际业务完成，不能把它当作最后一次通信完成时间。`sample` 合并端口窗口时保留全部发送状态更新，可改变 rank 本地时钟；未采集该表的旧切片不能补造它。完整字段和覆盖口径见[输出说明](../../docs/record_storage.md#端侧发送资源状态)。
