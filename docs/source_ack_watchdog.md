# 运行中的源端确认超时通知

`source_ack_watchdog`在LLMServingSim Scale-Out服务中，为每次实际SEND post启动一次确认期限。实际源端发送确认到达则取消；期限到达仍无确认时，由同一ns-3时钟发出`SOURCE_ACK_TIMEOUT`，Python立即接收、验证并保存。默认关闭，不发网络包、不改变原重传、阶段门限或完成条件，也不启动备用执行。

## 配置和使用

服务顶层增加：

```json
"source_ack_watchdog": {
  "schema_version": "moe-source-ack-watchdog/1",
  "timeout_ns": 20000,
  "max_messages": 128,
  "max_notifications": 128
}
```

要求`moe-scaleout-service/1`或`/2`、显式持久`transport`与[执行事件](execution_events.md)。四字段必须齐全，无默认期限；`timeout_ns`取1—1,000,000,000纳秒，两个数量上限取1—65,536。`max_messages`限制整次服务已启动watchdog的消息总数，不是并发数。超限明确失败，保留前缀，不采样或扩限。消息上限在实际post时检查，失败可能发生在运行中；不是硬件容量。

同一消息最多通知一次，之后继续等待真实确认，不重新启动watchdog。共享消息不会按token或专家数重复通知。从ASTRA提交SEND到确认的等待可能包含源端排队和传输恢复，不等于从首个实际数据包发送开始计时。

仍使用`scaleout rdma-run/rdma-check`，需要支持该选项的新后端；旧后端拒绝未知选项，不静默降级。[公开示例](../examples/scaleout_source_ack_watchdog/README.md)可独立准备128端点Clos输入。原生入口接受`--source-ack-watchdog CONFIG.json`，Python按配置生成该文件。新机器按既有`scaleout rdma-build`构建；本轮维护用增量编译不替代公开构建流程。

## 通知能证明什么

通知只证明：在该消息实际post后的声明期限内，尚未执行源端发送确认回调。它不读取故障注入位置、故障结束时刻、交换机丢包或目标接收状态，也不证明丢包、设备故障或专家结果缺失。

例如SEND post在2ns、数据8ns到达、确认10ns到达，若期限长度7ns，9ns仍会通知，尽管接收端已有数据。后续策略须检查当时合法可见的贡献进展与副本资源，不能一收到通知就重算。这个成熟超时机制的功能接入本身不作为论文创新或收益。

确认与期限恰好同纳秒时，保留Simulator实际顺序：确认先处理则取消；期限先处理则通知，随后仍由真实确认完成。接收回调不取消源端期限。原消息排空、原图退休和逻辑贡献到齐继续分开，不能用通知或备用结果生成ACK。

## 文件和核验

- `source_ack_timeouts.jsonl`：在线验证通过的通知，失败时保留前缀。
- `source_ack_watchdog.json`：已消费前缀的消息、确认、取消、通知、pending timer及未确认数量；未排空前缀不能当成功。
- `result.service.source_ack_watchdog`：原生独立计数与配置。默认关闭时无新增文件/字段。

通知schema为`moe-source-ack-timeout/1`，含`type=SOURCE_ACK_TIMEOUT`、连续`sequence`、绝对纳秒`time_ns`、此前执行事件数`observed_events`，以及`graph_id/rank/message/send_post_ns/deadline_ns`。每条最多1024B，总文件受`max_notifications×1024B`约束。身份对应实际消息，不冒充专家或张量身份。原始帧保留在`native.stdout.log`，不是`DECIDE`，Python不回复COMMIT。

这是单向采集，不暂停Simulator等待Python。`time_ns`表示原生发出通知的时刻，不保证Python消费时后端仍停在该时刻；不能用它回填过去的恢复调度。后续若允许Python提交恢复动作，必须另建同一仿真时刻的同步决策入口，明确验证回复及资源准入。

`SourceAckObserver`按已验证执行事件与通知的原始交错顺序核对。`rdma-check`将两个保存流逐行与原生帧对应，重放post、确认及期限，拒绝提前、迟到、重复、错误身份/前缀、遗漏通知与伪造计数。执行事件另与独立ASTRA日志/实际消息回调核对。跨流同纳秒顺序保留；真实确认先于同刻期限时允许没有通知，不从事后时刻猜另一种顺序。

尚无在途恢复响应、备用图插入、共享GPU帮助执行或净收益；仍须实现这些语义，再与相同观测、资源和副本预算的普通备用请求比较。
