# 原专家贡献与实际执行绑定

LLMServingSim Scale-Out服务可将同一次执行的请求、原始MoE层、token和目标rank上的完整专家贡献集合，绑定到实际输入、计算、结果和退出事件。它使用[实时执行通道](execution_events.md)驱动[贡献状态核心](contribution_attempts.md)，只观察原尝试，不发起备用任务或改变原有阶段门限。

## 配置与范围

服务配置顶层显式加入：

```json
"contribution_tracking": {
  "schema_version": "moe-contribution-binding/1",
  "namespace": "my-study-v1",
  "max_invocations": 64,
  "max_bundles": 512,
  "max_lifecycle_events": 6144,
  "max_report_bytes": 4194304
}
```

同时要求服务为`moe-scaleout-service/1`或`/2`，提供可校验的`route_replay`、`execution_events`、持久`transport`、`ep_transport_flush/1`和`deepep_direct_bf16/1`的`rank_reduced/1`模式。默认关闭；不接受缺少前提时静默降级。沿用`scaleout rdma-run/rdma-check`，可独立准备的输入见[128端点Clos功能示例](../examples/scaleout_contribution_tracking/README.md)。

`namespace`是用户声明的仿真输入代际，须符合1–128字符ASCII字母、数字或`_.:-`；不同实验输入代际应区分。跨进程录制并不因此获得身份认证。三个累计数量上限分别最多4096调用、32768贡献包、524288生命周期事件；报告字节最多33554432。平台在启动后端前按原始选择核算调用/贡献/事件数，每图注册时再次检查；报告写入前逐记录检查字节上限。超过限额明确失败并保留前缀，不扩大预算或补造完成。

本模式保留原图的聚合计算：一个rank的`L*.experts`可能同时计算多个token的专家贡献。绑定记录会引用同一节点完成时刻，不能据此声称已经观测到每个专家独立内核或有独立计算引擎。共享Dispatch/Combine消息也只发一次；每个调用持有的是这条实际消息的逻辑义务引用，不是额外传输或独占物理缓冲。

## 哪个时刻代表什么

| 实际事件 | 对贡献状态的作用 |
|---|---|
| 源rank的`L*.pack`启动 | 登记该token的所有原尝试，打开原任务图引用 |
| 本地pack完成 / 远端Dispatch接收回调 | 本地 / 远端输入就绪 |
| 目标rank的`L*.experts`启动、完成 | 对应贡献包开始、完成专家计算；全部引用同一真实聚合节点 |
| 本地`combine_reduce`完成 | 本地贡献结果可以提交 |
| 远端Combine接收回调 | 完整的目标rank贡献集合到达请求来源，可提交；不是Dispatch输入到达 |
| Dispatch/Combine SEND实际post、源确认 | 打开、关闭对应消息义务引用；接收回调不关闭源端义务 |
| 原图实际退休 | 关闭图引用，已完成且所有消息义务关闭的原尝试退休 |

结果按第一有效回调提交，每个应有专家恰被覆盖一次。当前每包只有一个原尝试，没有实现副本竞争。`logical_complete_ns`早于原图退出时，也不会让ASTRA提前推进、伪造ACK或删除原传输。图引用是保守的实际图生命周期约束，不证明所有真实GPU内存和CQ资源都已建模。

## 输入身份的证据边界

原始路由JSON只有专家选择，没有激活张量、专家权重或门控系数。绑定使用`moe-contribution-attempts/2`，显式区分：

- `request_id`由原请求ID、file_id、原始路径和路由文件校验身份共同形成，不仅使用可复用slot。
- `source_layer`为原JSON层号；`token_coordinate={kind:route_decode_entry/1,source_entry,source_token_row}`定位原Decode入口及行，不猜测绝对词位置。
- `input_identity={kind:simulation_lineage/1,namespace,graph_id,producer_rank,producer_node}`表示仿真中pack输出来源，**没有`input_sha256`字段**。
- `execution_sha256`仅对声明的model、compute和token_transport执行约定取规范JSON摘要；没有加载或验证权重。
- `routing_sha256`仅对声明的专家选择顺序和原始层号取摘要；没有门控系数或张量数值证明。

每个token按其实际专家放置分成不重叠的目标rank贡献集合，恰好覆盖原始Top-K；实例中的provider仅为原所有者。不能从这些声明推断另一张卡已经具有同版本副本。旧`moe-contribution-attempts/1`及其独立CLI不变。

## 文件与核验

`contribution_calls.jsonl`按call名称排序，每行`moe-bound-contribution/1`，包含`call/binding/spec/state`。binding保存源rank、slot、逻辑层、原路由来源、每包专家/目标rank以及pack、expert、归并节点和Dispatch/Combine消息ID。state是核心的全部生命周期记录，时间来自同一次已验证原生事件，没有手填通信时刻。

`contribution_summary.json`为`moe-contribution-binding-report/1`，分别记录调用数、贡献包数、实际/预留生命周期事件数、结果到齐调用数、原尝试已退休调用数、原生事件数及服务排空时刻。失败前缀可能有已到齐结果但未退出原任务；没有排空或缺少摘要时不能当作完整成功。

`rdma-check`先将实时事件与独立ASTRA日志、实际消息回调和退出时刻核对，再从原JSON/声明放置重新建立贡献绑定，逐记录比较JSONL、摘要和完整覆盖。它验证模型中的身份、因果和记录一致性，不认证张量数值或商用RDMA硬件。

后续仍需在线故障检测、合法备用执行、共享GPU/NIC竞争、跨通信域访问、迟到结果与原连接清理，才能评价局部恢复相对于已有副本/备用请求的净收益。
