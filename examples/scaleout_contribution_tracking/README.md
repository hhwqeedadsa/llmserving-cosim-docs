# 128端点Clos的原贡献跟踪示例

四份不同人工来源、两个EP64组、E256/Top8、两层两步。继续使用真实LLMServingSim/ASTRA调度和单一原生ns-3时钟，声明计算成本。本例检查原尝试的生命周期，不是完整R1、硬件测量或恢复算法收益。

需要已安装的frontend环境和支持[实时事件](../../docs/execution_events.md)的原生服务。每次准备和执行均使用新目录，不需要旧运行产物或原始真实数据账户。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_contribution_tracking/prepare.py \
  --output /path/to/new-input --namespace my-study-v1

PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-tracked.json \
  --protocol /path/to/new-input/rdma.json --topology /path/to/new-input/topology \
  --backend /path/to/current/native-service --protoc /path/to/protoc --output /path/to/new-normal

PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-normal
```

准备程序复用版本化的[按目的rank去重输入生成器](../scaleout_token_transport/README.md)，再增加明确的跟踪预算，并生成`rdma-transient.json`：仅GPU6前20us丢弃目的DATA，控制包保留。需要故障条件时，把上面的protocol改为该文件，output改为另一个新目录；不能把它描述为全网100%丢包。

128个端点共用Clos：16台服务器×8、8ToR/8Agg/4Core、168链路、单rail/plane、各层400G和每链路1us；只有四个独立来源请求，不是128卡满载。沿用SR及参考DCQCN，不改恢复参数。协议内native预算60秒、RSS1GiB、地址2GiB、模拟5ms及产物128MiB；仍须按[开发说明](../../docs/development.md)安排宿主累计预算。

查看新增`contribution_calls.jsonl`和`contribution_summary.json`：输入到达、专家计算完成、完整贡献返回和原尝试退休分列。`logical_complete_ns`不是整组任务完成时刻，也不会替代原传输ACK。完整格式和身份边界见[绑定说明](../../docs/contribution_binding.md)。
