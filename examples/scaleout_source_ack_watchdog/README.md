# 128端点Clos源端确认超时示例

本人工功能例在贡献跟踪上增加在线确认期限。四份不同人工路由、两个EP64组、两层两步，仍用同一128端点Clos；没有真实GPU、完整R1或生产故障率。两个条件各运行独立服务，不能把两次执行合成共享网络。

在仓库根目录、已配置Python依赖与后端后执行（`/path/to`替换为自己的新路径）：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_source_ack_watchdog/prepare.py \
  --output /path/to/new-input --timeout-ns 20000
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-watchdog.json \
  --protocol /path/to/new-input/rdma.json --topology /path/to/new-input/topology \
  --backend /path/to/current/native-service --protoc /path/to/protoc \
  --output /path/to/new-normal
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-normal
```

局部故障使用`rdma-transient.json`并另选全新输出目录。它仅在前20微秒丢弃发往GPU6的数据包，控制保留；不是全网丢包。输入、SR、参考DCQCN、计算和门限保持原声明。

查看`source_ack_timeouts.jsonl`和`source_ack_watchdog.json`。无通知时也应核对所有确认及pending timer清零。通知表示SEND提交20微秒仍未确认，不代表接收端没收到，不触发重传或备用计算。改变`--timeout-ns`会改变敏感度和误报风险，不能据此声称优化收益。约束、同刻顺序与格式见[接口文档](../../docs/source_ack_watchdog.md)。
