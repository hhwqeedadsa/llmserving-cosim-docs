# 多生产者共享发送队列

这个人工小例验证预约、描述符就绪、顺序准备、发布与网络完成的区别。两服务器各四个逻辑GPU，跨机链路100Gb/s、每跳传播1微秒，MRT1每包64B载荷；没有GPU硬件类型校准，不是完整Hybrid或推理性能测量。

先按[用户指南](../../docs/user_guide.md)用固定工具链完成新的 `scaleout rdma-build`，backend使用其中transaction_session路径。下面目录均须尚未存在，`/ABS`替换为实际绝对路径，输出卷至少保留8GiB空闲。

```bash
PYTHONPATH=/ABS/REPO/python python3 -B -m moe_ub_cosim scaleout topology \
  --config /ABS/REPO/examples/scaleout_staged_publication/topology.json \
  --output /ABS/NEW_TOPOLOGY

PYTHONPATH=/ABS/REPO/python python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /ABS/NEW_MONITOR --rss-bytes 1073741824 \
  --address-bytes 2147483648 --artifact-bytes 67108864 \
  --wall-seconds 60 --min-free-bytes 8589934592 --artifact-root /ABS/NEW_RUN -- \
  python3 -B /ABS/REPO/examples/scaleout_staged_publication/run.py \
  --topology /ABS/NEW_TOPOLOGY \
  --protocol /ABS/REPO/examples/scaleout_staged_publication/rdma.json \
  --config /ABS/REPO/examples/scaleout_staged_publication/work.json \
  --backend /ABS/BUILD/output/moe-rdma/ns3.42-moe-rdma-transaction-session \
  --output /ABS/NEW_RUN
```

连接0包含两个put及两次原子加，连接1独立传送8B。控制器先准备连接0后面的三个描述符，暂不准备第一个；此时连接0不能发布。连接1完成后才准备连接0第一个描述符，由最后一个非聚合操作发布整个前缀。接收端首次应用put后，前两个生产者再发布较早前缀，均不重复发送。这是声明的人工顺序，没有填入假想GPU准备耗时。

## 修改控制器

公开类为 `moe_ub_cosim.transaction_session.NativeTransactionSession`。初始operations为空，根模式为 `submission_model={"mode":"staged_prefix/1"}`；在receive返回的决策中通过commit控制，宿主处理时间不计入ns-3时间。

| 调用或回调 | 含义 |
|---|---|
| `commit([op, ...])` | 注册；依赖满足后预约并产生submitted |
| `commit([], prepare_operations=[id, ...])` | 必须此前收到submitted；等待真实槽位信用后准备描述符 |
| descriptor_ready | 自己的描述符就绪，前面的生产者可能仍未就绪 |
| prepared | 连续ready前缀已覆盖该操作，顺序ready更新可完成 |
| `commit([], publish_operations=[id, ...])` | 必须此前收到prepared且为非聚合操作；发布至max(已有前缀,ticket末尾) |
| applied/request_acked/response/completed | 目标更新、请求ACK、原子回复、最终事务完成，分别记录 |

prepare与publish每操作各接受一次；数组内重复、未知/local/本次才注册操作、提前发布、聚合操作发布都拒绝。拒绝保留Python待提交决策。省略数组不自动准备或发布。队列深度限制准备但未完成的工作数，释放来自实际事务完成。wake_on/wake_groups可订阅descriptor_ready，静态after沿用prepared边界。

普通rdma-transaction-probe和依赖自动前端rdma-transaction-session在创建输出前拒绝此模式，需编写如本例的控制器并在公开host_probe下运行。默认和explicit_prefix/1保持。阶段控制不占用GPU串行lane；完整Hybrid控制/Combine、真实SM成本和GPU内存一致性仍未建模。

## 结果

result.json保存实际网络、目标窗口、源保护和回调；control.jsonl保存逐次决策及命令，native-command.json保存输入和后端hash。descriptor_ready_ns为描述符就绪，prepared_ns为连续前缀ready，未发生为−1。

transactions.submission.publications中requested为请求的排他ticket末尾，previous/exclusive为发布前后前缀；相同表示未推进，不能把记录数当物理门铃次数。源窗口从submitted保护到completed，字节在published捕获。全聚合且无后继发布的尾部会保持未完成，截止保存失败结果，不自动flush或补ACK。MRT1是功能载体，不是GIN逐位编码。
