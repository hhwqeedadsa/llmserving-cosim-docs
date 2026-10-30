# 查看一个快速备用是否推动整个请求

先按[独立128端点Clos消息缓冲示例](../scaleout_message_buffers/README.md)生成自己的输入并完成`rdma-run`。默认例无需历史运行目录或真实数据账户；有已完成的相同格式服务时直接读取即可，不必为诊断重跑网络。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout contribution-frontier \
  --run /path/to/new-run --output /path/to/new-frontier
```

查看`summary.json`的`calls_with_recorded_frontier_advance`，再检查`calls.json`中的原件/赢家全部就绪、全部并列最后贡献及实际merge时间。单个备用早返回而该值为0时，说明固定记录中的完整集合就绪并未提前，不可直接宣布请求加速。

`bundles.csv`还分列输入等待、Dispatch门限、GPU FIFO等待和缓冲存活。未来消费者要读取所有结果时，已经释放的赢家结果需要明确保留引用、复制或提前归并进有生命周期的累加器；同纳秒事件标记未知，不猜测顺序。诊断不执行消费者，也不改变前端任务、原阶段门限或原ACK。字段、适用条件与证据边界见[说明](../../docs/contribution_frontier.md)。
