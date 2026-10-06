# 操作阶段与等待字节统计

沿用 [M13 场景](../m13_endpoint_service/scenario.yaml)：三个 rank、一轮 Decode、一个 dense 与三个 MoE 层，原生 STORE/LOAD、真实供数通知、逐包喷洒、CBFC，以及节点 4 出口 4 的服务限速。此目录的 `observation.json` 开启操作阶段和队列统计，观察同一出口，每窗 127 ns，总观测至 1 ms，并声明事件和输出预算。

普通运行使用以下公开入口，路径相对配置文件解析；更换解析目录时同步改 `run.json.inputs`：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario resolve \
  --input fixtures/m13_endpoint_service/scenario.yaml \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-operation-queue-input
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim run \
  --config fixtures/m14_operation_queue/run.json --output runs/my-operation-queue-run
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim validate \
  --run runs/my-operation-queue-run
```

输出新增 `observed` 下的 `endpoint_stages.jsonl` 和 `queue_windows.csv` 逻辑表，均经清单定位到 gzip JSONL。定义及读取方式见[记录格式](../../docs/record_storage.md#v04-操作阶段与等待统计)。原生请求的消费方由操作语义决定，LOAD 响应就绪不能记到供数方。旧端侧区间仍保持原定义，准备阶段另行记录。

组合验证入口：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_operation_queue.py \
  --build-dir build/v04-platform --output-dir runs/my-operation-queue-check
```

检查关闭/开启统计及关闭真值输出三种配置的真实执行；逐项核对节点、传输、物理包与时刻摘要；按实际节点和传输独立核对 177 条阶段事件。队列采用逐本地纳秒的独立枚举，对照后端积分和公开输出，覆盖窗口开始时非空、跨窗等待、短尾、同刻变化及没有发送的非空窗口。新时钟与任意窗口的详细重采样、完全相同图重放、旧数据缺状态历史拒绝和事件预算不足拒绝均包含在检查中。

最终小例结束于 47,193 ns，物理发送 2,088 次；205 条队列状态产生 92 个非零等待窗口，其中 41 个没有发包。关闭与开启统计的宿主运行阶段分别约 0.894 / 0.884 s，采样进程树峰值约 105.2 / 104.0 MB，整份运行文件约 1.551 / 1.564 MB。短小运行及采样有波动，这些数值只能说明本次成本，不能据此声称统计让程序更快或更省内存。详细数据见 [result.json](result.json)。

本次构建的队列积分对象为 176 B/端口，当前窗为其中的 48 B。仅此对象在 16,128 个端口约 2.84 MB，尚不包括订阅映射、压缩缓冲及其他执行状态。若每端口按 1 µs 在 75.324 ms 内密集保存队列窗口，将有约 12.15 亿行；以小例平均行宽向上取整 288 B 估计，未压缩约 350 GB。这是明确假设下的输出估算，不是大场景实际结果；应按研究范围选择端口、区间与周期，不能只按计数器大小估计成本。

最终检查另含 24 项 Python 单元检查、C++ 独立积分与 VOQ/FIFO 控制包状态检查、既有 WRITE 公开流程和原生多路径回归。命令、目录、版本、退出码与资源日志均由摘要定位。首次组合检查的测试脚本误从执行传输行直接读取 `operation_id`，修正为按 `transfer_id` 连接已保存计划后保留全部比较；原失败日志保留。通过的是功能与被动性，目标硬件采集接口、完整规模和校准不由这些结果证明。
