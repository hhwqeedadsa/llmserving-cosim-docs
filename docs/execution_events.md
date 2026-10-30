# 实际执行的实时事件

源端确认超时使用独立、显式的[watchdog通知](source_ack_watchdog.md)，不向下述OBSERVE种类增加合成消息完成。通知来自实际ns-3期限，原执行事件继续只反映真实节点与回调。

LLMServingSim Scale-Out服务可在执行中向Python发送节点和消息事件，用于区分数据交付、发送确认以及任务图退出。默认关闭。它只观察同一次ASTRA/ns-3执行，不改变依赖、阶段门限、选路、协议参数或仿真时间；不是专家副本执行或恢复策略。

在已有`rdma-run`服务JSON的顶层加入：

```json
"execution_events": {
  "schema_version": "moe-execution-events/1",
  "max_events": 32768
}
```

继续使用原来的`scaleout rdma-run/rdma-check`命令和支持该选项的新原生服务。完整的128端点Clos输入可以按[按目的卡去重示例](../examples/scaleout_token_transport/README.md)准备，再编辑生成的`service.json`；其来源是四份不同人工路由，检查功能，不代表真实R1性能。旧程序不支持该原生参数，会明确失败，不会默默忽略。

`max_events`必须为1至262144的整数。所有任务图的必要事件数须落在这一累计上限内；不是每图重新计数。录制超限或收到错误事件时终止并保留日志，不采样、覆盖或补造完成。宿主运行/内存/磁盘预算仍须通过原协议配置限定；每事件几百字节，额外记录会增加宿主耗时和文件。

## 事件的含义

原生程序在实际调用点通过独立控制输出发送`OBSERVE`帧。Python逐行读取并立即验证，只有`DECIDE`要求发送`COMMIT`；观察事件没有回复，也不调用前端调度器。

| `kind` | 发生位置与含义 |
|---|---|
| `node_issue` | ASTRA已准许该节点启动，进入实际dispatch之前；不是排队入图时刻 |
| `node_compute_start` | 仅显式`/2`：原COMP实际占用共享GPU FIFO引擎的时刻，包含零时长COMP，不包含GATE |
| `node_complete` | ASTRA接受该节点有效回调之后、释放资源和解锁后继之前 |
| `message_send_post` | 实际逻辑SEND已提交，进入共享传输服务之前 |
| `message_recv_post` | 实际逻辑RECV开始等待，不另发一份数据 |
| `message_recv_complete` | 完整数据已接受且RECV已提交，接收端即将调用ASTRA回调；不是首包到达或真实GPU可见性 |
| `message_send_complete` | 共享传输服务已向源端确认该消息，发送端即将调用ASTRA回调 |
| `graph_retired` | 所有成员节点、收发回调及ASTRA资源已完成，整组任务图退出 |
| `service_drained` | 所有任务图已退出且网络服务完成排空，将停止仿真 |

发送确认可能早于接收应用提交RECV，因此不强制“接收回调一定早于发送回调”。接收完成也不会伪造发送确认。节点完成、整组图退出与网络排空各自记录，不能混作一个时间。

零耗时的阶段门限使用ASTRA外部完成事件路径，issue到complete可以有等待。普通非零COMP的耗时仍来自声明计算成本。此接口不增加真实张量、权重证明或硬件测量。

## 文件和读取接口

开启后新增`execution_events.jsonl`和`execution_observation.json`。前者只保存在线验证通过的事件；原始完整/错误帧仍在`native.stdout.log`。后者是已验证前缀摘要，失败或宿主杀进程可能不含末尾摘要；`service_drained_ns=null`表示未观察到排空，不能当作成功。

每事件格式为`moe-execution-event/1`，包含`type=OBSERVE`、从0连续递增的`sequence`、同一ns-3绝对纳秒的`time_ns`和`kind`。节点事件另含`graph_id/rank/node`；消息事件另含`graph_id/rank/message`；图退出含`graph_id`。同一纳秒以sequence保留实际回调顺序。ID来自实际提交图，不是请求或专家身份，更不是张量哈希。

`result.service.execution_events`给出有效配置和原生已发事件总数。`rdma-check`重新读取JSONL，对照独立ASTRA日志、消息post/回调结果、图退出和排空时刻，检查事件总数与在线摘要。部分前缀不是完整验收。关闭记录时不生成这些文件或结果字段。

平台接口`moe_ub_cosim.execution_events.ExecutionObserver(option)`提供`register(wave)`、`accept(event)`和`snapshot()`。register只接受调用者实际已提交的任务图；它不生成执行时刻。事件验证包括依赖、计算最小时长、消息两端身份、重复/乱序以及未完成资源退出。可选[贡献绑定](contribution_binding.md)将这些事件接入`ContributionAttempts`；合法的额外执行见[共享GPU/NIC备用接口](inflight_helpers.md)，不能靠改目的rank替代。

## 显式记录实际计算开始

在已经开启`inflight_helpers`的共享计算服务中，将上述配置的`schema_version`设为`moe-execution-events/2`，继续使用相同命令；后端须支持`--execution-events-v2 MAX_EVENTS`。默认`/1`保持原事件、摘要及贡献口径，可继续核验历史结果。缺少共享计算的`/2`输入和旧后端均明确拒绝。

`/2`每个原COMP新增一个`node_compute_start`，直接由FIFO开始回调发出，不额外调度事件或在完成后回填。所有OBSERVE帧改用`moe-execution-event/2`，摘要为`moe-execution-observation/2`并增加`compute_starts`。累计预算须额外覆盖全部COMP；GATE、SEND/RECV没有该事件。

`node_issue → node_compute_start`为本模型的GPU队列等待，`node_compute_start → node_complete`必须恰等于原声明计算成本。零成本COMP也可排队；GATE的等待仍属于阶段依赖。接收和ACK可在排队期间继续发生。贡献的`compute_begin_ns`在此模式取实际开始；`/1`仍取原issue，不能混用两者计算GPU占用。

`rdma-check`另外逐项核对实际开始与`shared_compute.tasks.start_ns`，检查原COMP完整覆盖、实际时长与FIFO顺序。自包含的128端点Clos竞争用例见[示例](../examples/scaleout_compute_start/README.md)。这些开始时刻仍是声明计算模型，不是CUDA或GPU实测。
