# 128端点Clos共享资源备用执行

四份独立人工路由、两个EP64组、每rank每层4个专家，两层两步。128个模拟端点，单plane/rail，各层400Gbps、每链路1us；不是完整R1、真实路由性能测试或GPU测量。普通与备用任务共用同一计算队列、NIC和网络，原消息与阶段门限保留。

仓库根目录执行，替换示例绝对路径：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B examples/scaleout_inflight_helpers/prepare.py \
  --output /path/to/new-input
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config /path/to/new-input/service-observe.json \
  --protocol /path/to/new-input/rdma.json --topology /path/to/new-input/topology \
  --backend /path/to/current/native-service --protoc /path/to/protoc \
  --output /path/to/new-normal
PYTHONPATH=python .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-check \
  --run /path/to/new-normal
```

三组分别运行和检查，每组独立新输出目录：

| 条件 | 服务配置 | 网络配置 |
|---|---|---|
| 正常共享计算 | `service-observe.json` | `rdma.json` |
| 局部故障共享计算 | `service-observe.json` | `rdma-transient.json` |
| 相同故障加一次备用 | `service-helper.json` | `rdma-transient.json` |

局部故障只在前20us丢弃发往GPU6的DATA包，ACK等控制保留。选择性重传和参考DCQCN均已开启。准备脚本在三组声明相同持久流；服务配置只改变策略，不改变副本、计算或网络资源。

查看`helper_events.jsonl`、`helper_control.json`、`result.service.shared_compute`及`contribution_calls.jsonl`，分别比较贡献逻辑就绪、原图完成和网络排空。备用先到并不自动释放原图；没有业务收益时应保留负面结果。详细字段、预算和功能边界见[接口](../../docs/inflight_helpers.md)。
