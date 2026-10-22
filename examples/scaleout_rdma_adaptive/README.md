# 源端自适应超时与推理通信诊断

本例在[固定超时恢复](../scaleout_rdma_recovery/README.md)上增加可选RTT（往返时间）估计。它使用源端实际发送与ACK/NACK反馈，按Karn原则排除重传歧义；不读取接收端或交换机真值。每条QP独立，最多保留一个测量包，没有共享历史或硬件校准。目的在于研究恢复参数如何影响推理通信，不能承诺所有场景更快。

## 运行

使用项目Python环境，设置`PYTHONPATH=python`，先按[RDMA小例](../scaleout_rdma/README.md)构建当前原生后端。`$COSIM_PROBE`、`$COSIM_SERVICE`从新构建`backend.json`读取，`$COSIM_PROTOC`指向已有Protobuf编译器。输出目录须不存在，运行卷保留至少8GiB，预算沿用固定恢复示例。

```bash
python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/adaptive-topology
python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/adaptive-topology --config examples/scaleout_rdma_adaptive/adaptive-final-ack.json \
  --messages examples/scaleout_rdma_recovery/messages.json \
  --backend "$COSIM_PROBE" --output /dev/shm/adaptive-probe
python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/adaptive-probe --output /dev/shm/adaptive-diagnosis.json
python -m moe_ub_cosim scaleout rdma-run \
  --config examples/scaleout_rdma/service.json --topology /dev/shm/adaptive-topology \
  --protocol examples/scaleout_rdma_adaptive/adaptive-service.json \
  --backend "$COSIM_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/adaptive-service
python -m moe_ub_cosim scaleout rdma-check --run /dev/shm/adaptive-service
```

目标消息和故障规则对应原公开小例；改模型、batch或分组后要从实际无故障图重新选目标。`adaptive.json`是无故障基线；`short-bootstrap.json`故意声明2μs初始超时，用于观察误重传和采样冷启动限制，不能当作推荐参数。

## 配置

在完整RDMA JSON的`recovery`对象内可选增加`rtt_estimator`。不增加时仍使用原固定超时；原生模式只接受`{"mode":"native"}`。

```json
"recovery": {
  "mode": "go_back_n",
  "initial_rto_ns": 40000,
  "max_rto_ns": 160000,
  "max_retries": 3,
  "max_events": 100000,
  "rtt_estimator": {"kind": "karn", "min_rto_ns": 1000, "granularity_ns": 1}
}
```

| 字段 | 意义与范围 |
|---|---|
| `kind` | 目前只支持显式`karn`；其余两个字段也必填。 |
| `min_rto_ns` | 学习后的超时下限，1至`initial_rto_ns`。示例1μs不是硬件建议值，应按场景声明或校准。 |
| `granularity_ns` | 估计器时钟粒度，1至`max_rto_ns`；不会改变ns-3全局时钟或链路延迟。 |

每次新ACK进展将超时重置为`clamp(SRTT + max(granularity,4×RTTVAR),min_rto,max_rto)`；没有样本时用初值，连续无进展仍指数退避。重复/旧反馈不延后计时。平均与偏差的更新顺序、整数向上舍入、歧义屏障及证明见[形式化说明](../../docs/communication_fragility.md)。

短消息可能尚未获得有效样本就结束；低初值导致重传后，相关包的反馈会被保守排除，自适应不能保证自动纠正初值。排队分布突然变化也可能使估计失效。应对照固定模式的完成/失败、额外字节、有效样本和推理推进，而非只看RTO数值。

## 输出

`result.json.recovery.states[*].rtt`给出样本数、作废测量包数、平滑RTT、偏差、基准RTO和歧义屏障；`rtt_events`只记录源实际包、反馈和超时。它与源计时事件、接收端重复ACK事件共享`max_events`。关闭/截断记录不改变协议；完整日志才逐事件重放核对估计，截断时明确不能重建。

`rdma-diagnose.recovery.silence_bounds`是无后续新ACK进展时的条件终止证书：K次重传后，失败截止为区间起点加`Σ min(2^i×reset_rto,max_rto)`，i=0..K。它既不是请求SLO，也不表示一定恢复成功；准入等待和首次发送之前没有此计时保证。

`source_credit_obstructions`按当前所有者核算消息槽、逻辑字节和候选队首，指出当前是否可发及已到达却未确认的占用者。该结果只解释执行末态，不把整个等待都归为网络排队。来源和记录范围见[输出数据](../../docs/record_storage.md)，验证见[功能验收](../../fixtures/scaleout_adaptive/README.md)。
