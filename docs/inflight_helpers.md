# 运行中的备用专家执行

`inflight_helpers`为LLMServingSim Scale-Out服务提供显式可选的备用贡献执行。源端确认超时后，原生ns-3在当前时刻暂停事件推进，发出`HELPER_DECIDE`；Python只能回复同一决策编号和时刻的`HELPER_COMMIT`。备用输入、专家计算、本地归并和结果回传真实执行，并进入贡献的一次提交及资源退休检查。默认关闭。

原图的Dispatch/Combine组门限、原始SEND/RECV、ACK和图退出继续保留。因此备用贡献先返回，可能仍不能推进原图和推理请求。该接口用于检验实际资源、额外流量和完成约束，不表示已经实现业务恢复，也不作为新算法收益。

## 输入与使用

在已有服务配置顶层增加：

```json
"inflight_helpers": {
  "schema_version": "moe-inflight-helpers/1",
  "policy": "first_missing_bundle/1",
  "max_attempts": 1,
  "max_events": 12,
  "max_compute_tasks": 4096,
  "max_decisions": 128
}
```

六字段均必填。服务须有路由重放、持久`transport`、[执行事件](execution_events.md)、[贡献跟踪](contribution_binding.md)、[确认期限](source_ack_watchdog.md)、`ep_transport_flush/1`阶段完成和`rank_reduced/1`回传，并显式部署至少两个完整EP组。EP指专家并行。各组的专家归属依据原始部署或显式等容量放置映射，不临时迁移专家或假装副本已存在。

`observe_only/1`仍经过同刻同步回复，但不提交备用工作，供同资源对照使用。`first_missing_bundle/1`在超时消息对应的候选中按稳定身份排序，选择第一项源端尚未获得结果、输入pack已完成的完整目标rank贡献包，每次最多一项。它不读取故障注入真值、接收端输入状态、专家远端进度或未来记录。该简单策略只是功能基线，不是最优放置或新恢复算法。

`max_attempts`为整次服务1—128次，`max_events`为1—65536且至少为次数的12倍，`max_compute_tasks`为1—131072项，`max_decisions`为1—128次。数量都是累计上限。原图COMP和备用计算共用计算任务额度；贡献生命周期需另预留每次备用12项事件。超限失败并保留已有记录，不静默扩容。准备阶段还检查候选传输绑定、稳定消息身份、原始消息加备用工作量上限和完整专家集合。

使用既有`scaleout rdma-run/rdma-check`，需要支持`--inflight-helpers CONFIG.json`的后端。新机器沿用公开`scaleout rdma-build`构建。配置文件的相对路径按既有服务规则解析；输出目录必须为新路径。[独立准备示例](../examples/scaleout_inflight_helpers/README.md)不依赖历史运行产物或真实数据账户。

## 实际占用与限制

每个模拟GPU一个非抢占FIFO，普通COMP与备用专家/归并共用。已排队任务先于完成回调新解锁的任务执行；不同GPU可以并行。原始COMP声明成本保存在wave中，Chakra编码通过已有外部事件路径交给共享队列；零计算阶段门限独立等待，不占GPU。它是声明的功能模型，不是CUDA并发、SM占用或GPU硬件校准。

候选覆盖一个token在原目标rank上的全部专家贡献。选择其他完整EP组中持有同一集合的最小rank；源、原目标、备用提供者须不同。输入和回传按token布局计算字节，专家和本地归并采用相同声明单位成本。额外工作通过原有ServiceTransport发到同一个Fabric、同一Simulator时钟和既有NIC；须为所有候选方向显式声明持久流。各对照应使用同样的流、副本、预算和计算模型。

同步前端回复消耗宿主执行时间，仿真中没有建模控制通信、决策CPU和副本加载成本，不能据此声称真实系统零成本决策。来源只有专家选择，没有激活张量、权重或门控权值，因此只验证声明身份与依赖，不验证数值结果。当前仅为备用尝试本身接入生命周期；原图不会因为贡献逻辑到齐而提前释放门限或删除原连接。

## 输出与检查

- `helper_control.json`：每次真实同步请求及策略回复，含同一`decision_id/now_ns`；不允许回填过去。
- `helper_events.jsonl`：`moe-helper-event/1`，连续序号、绝对纳秒、此前原执行事件数、候选身份与阶段。
- `helper_observation.json`：消费完成的决策、选择、生命周期和退休汇总。
- `result.service.inflight_helpers`：原生候选、实际尝试及ACK/接收/退休计数。
- `result.service.shared_compute`：每个计算任务的物理rank、累计入队序号及ready/start/done纳秒；可核对排队和额外开销。
- `service_transport.messages`与完整包记录：原消息和额外输入/回传都对应真实连接字节区间，包头仍用stream身份，不改成语义消息身份。

每次备用包含accepted、input_post、input_ready、input_ack、compute_begin/done、reduce_begin/done、result_post、result_ready、result_ack和retired共12种事件。输入ACK与接收分开，结果到达与结果ACK分开；只有全部实际资源关闭后退休。原结果迟到可以被判为冗余，但仍须履行原消息与图退出义务。

`rdma-check`从原始交错帧重放可见前缀和确定策略，检查同刻身份与回复、共享FIFO无重叠/空转、完整原COMP覆盖、备用事件与实际传输post/receive/ACK逐项相等、实际逐包接收、贡献一次提交与资源退休。检查通过只证明这些执行事实，不能代替业务收益或硬件准确性。
