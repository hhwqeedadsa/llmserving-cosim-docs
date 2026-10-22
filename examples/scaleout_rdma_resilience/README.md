# 推理通信恢复与源资源保护

这组完整JSON用于原生RDMA Scale-Out入口。`history.json`让后续短QP参考同一源、目的地址的近期有效RTT或超时退避；`destination-protection.json`限制单一目的端占用，保留故障资源的同时让其他目的端有机会发出。它们不提供请求级容错或正确结果替代。

先按[原生后端说明](../scaleout_rdma/README.md)构建，设置`COSIM_NATIVE_PROBE`、`COSIM_NATIVE_SERVICE`为返回的两个可执行文件绝对路径，`COSIM_PROTOC`为已有编译器路径。基础命令为`PYTHONPATH=python python -m moe_ub_cosim`，从仓库根运行。以下输出必须是不存在的新目录，所在卷预留至少8GiB。

```bash
PYTHONPATH=python python -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_rdma/small-clos-100g.json --output /dev/shm/resilience-topology
PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-probe \
  --topology /dev/shm/resilience-topology --config examples/scaleout_rdma_resilience/history.json \
  --messages examples/scaleout_rdma_resilience/history-messages.json \
  --backend "$COSIM_NATIVE_PROBE" --output /dev/shm/resilience-history
PYTHONPATH=python python -m moe_ub_cosim scaleout rdma-diagnose \
  --run /dev/shm/resilience-history --output /dev/shm/resilience-history-diagnosis.json
```

恢复配置中显式增加：

```json
"history": {"max_entries_per_source": 16, "max_age_ns": 300000}
```

仅在`go_back_n`且配置了`rtt_estimator`时接受，缺省关闭。每源容量1..256，每条证据有效期1..1000000000ns；不同源独立容量和淘汰顺序，目的地址区分目的GPU与plane。首次实际发包时查询历史，取声明初值与历史值的较大者；不改变已存在QP的计时器。有效RTT样本产生的基准RTO、真实超时重传决策产生的退避RTO均可更新历史，仍受`max_rto_ns`限制。超时不是RTT样本；不能增加有效样本计数。

有效期内保留见过的较大RTO，证据更新刷新有效期，单纯读取不刷新；恰好到期即失效，超容量淘汰本源最近最少使用条目。最终快照可能保留未被再次访问的过期条目，访问时会先清除；它们不会被用作新QP初值。第一批通信没有历史，持续拥塞、不同ECMP路径或长期保守值仍可能产生重复流量或推迟故障检测。地址相同不意味着每次ECMP路径相同，当前不按未知未来路径分桶。

`admission`可显式增加：

```json
"per_destination": {"max_active": 1, "max_bytes": 4096}
```

要求策略为`destination_round_robin`，每目的端槽和字节上限均正且严格小于相应源总上限；源字节上限也须显式正值。它合计该源到同一目的GPU、所有plane的全部活跃逻辑消息，源本身仍共享总额度；不会增加资源，也不读取哪个专家发生故障。字节从准入一直占到真实确认且QP清理后。轮转跳过份额不足的目的端队首，不绕过同一目的端内部FIFO；单条消息超过份额会拒绝，不隐式拆分。机内本地传输继续不占外部RDMA额度。

将上述probe命令的配置与消息分别换成`destination-protection.json`、`protection-messages.json`，会得到**预期非零退出**：目的4的ACK持续丢失，目的6的健康消息先完成，随后故障重试耗尽停止整个运行。对失败目录照常执行`rdma-diagnose`，不能把诊断通过写成推理成功。

## 统一修改模型、batch、拓扑和通信策略

```bash
PYTHONPATH=python python -m moe_ub_cosim scaleout sweep \
  --config examples/scaleout_rdma_resilience/sweep.json \
  --backend "$COSIM_NATIVE_SERVICE" --protoc "$COSIM_PROTOC" --output /dev/shm/resilience-sweep
```

`sweep.json`给出五个显式条件：正常初值参考、组合策略、batch1、小模型形状和50G接入链路，每个都重新生成拓扑并执行真实前端与共享网络。基础文件路径相对清单所在目录解析。`patches`可改`service`、`topology`、`protocol`的嵌套字段：对象递归合并，数组整体替换，`null`删除已有可选字段；必填字段删除或非法类型在所有条件预检时拒绝。没有隐式参数笛卡尔积，最多32个明确条件。

| 想改什么 | 修改位置与边界 |
|---|---|
| batch、输入上下文、步数 | `patches.service.workload`；支持范围与ready-KV Decode约束沿用[服务输入](../scaleout_rdma/README.md) |
| 模型形状和专家数 | `patches.service.model`；专家须能按EP组放置；不自动生成硬件计算校准 |
| GPU类型 | `model.gpu_type`是描述标签，必须配合容量与计算成本；改名称不会自动得到另一显卡的真实时延 |
| EP分组 | `patches.service.parallel.groups`；须完整划分实际GPU，两个独立副本不代表训练DP同步 |
| 拓扑、rail和链路带宽 | `patches.topology`；层数/端口/连通性按[拓扑规则](../scaleout_topology/README.md)检查，协议ECN须覆盖所有速率 |
| 注入与恢复策略 | `patches.protocol.admission/recovery`；只能用平台已实现策略，不能填尚未实现的集合通信算法名 |
| 专家选择 | 合成参数或已有来源校验的`route_replay`；回放须满足请求、层与步数覆盖，不能把合成流量当真实热点 |

`comparison_group`可将相同业务、相同拓扑的策略条件归为一组；预检要求二者输入一致，执行后逐一核对共同实际提交图的hash。不同batch/模型要分组或不填，失败只提交的部分图不会被补成完整运行。

每条件缺省`expect="success"`；显式可选`recovery_retry_exhausted`或`simulation_time_limit`，只接受实际结果中相同原因的失败，其他非零退出一律中止清单。预期失败保留，不改变`passed=false`。不自动重试或提高预算，旧输出目录拒绝覆盖。

总预算`wall_seconds/rss_bytes/address_bytes/artifact_bytes`由外层宿主监控覆盖全部条件与子进程；各条件同时保留自己的预算，总磁盘上限可能比单例上限更严格。每次运行独立时钟，不能把条件耗时相加当一次推理。输入、解析后配置、源码/二进制hash、命令、退出与资源记录保存在根目录，每条件保留`run/`和`diagnosis.json`。`summary.json/csv`分别报告前端完成、网络停止/排空、提交逻辑量、实际payload、超时、样本和历史命中；CSV空白对应JSON的null，不表示零。

功能验收见[恢复与份额门](../../fixtures/scaleout_resilience/README.md)，机制依据见[条件证明](../../docs/communication_fragility.md)。
