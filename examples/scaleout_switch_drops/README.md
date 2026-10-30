# 观察交换机为什么丢包

此功能为非UB原生RDMA后端增加离线原因记录，用于区分人工故障与交换机转发、缓冲准入失败。默认关闭，不调整队列、PFC、ECN、选路、重传或完成条件。

## 配置与输出

在已有 `moe-scaleout-rdma/1` 或 `/2` 协议中增加：

```json
"switch_drop_capture": {
  "nodes": [],
  "start_ns": 0,
  "end_ns": 5000000,
  "max_events": 100000
}
```

`nodes=[]`选择全部交换机；也可指定至多256个不重复的实际交换机ID，不能填写GPU。时间单位为纳秒，详细窗口为 `[start_ns,end_ns)`，终点不超过协议模拟时间上限。`max_events`为1—100000。四种原因的计数覆盖全部交换机、整个运行期，不受详细记录的节点/时间筛选限制。

逐消息GBN和持久SR均可使用该配置。该字段不会启用恢复或替换前端。省略整个字段时结果不新增相关记录。

`result.json.switch_drop_capture`使用 `native-switch-drop-capture/1`：

| 字段 | 含义 |
|---|---|
| `time_ns`, `node` | 实际丢弃时刻与交换机ID |
| `ingress_port`, `egress_port` | 接口编号；无路由时出口-1，缺少入口tag时入口0 |
| `priority`, `reason` | 优先级；原因1入口准入、2出口准入、3无路由、4链路关闭 |
| `packet_uid`, `message_id` | 包来源编号与逻辑关联；持久传输中后者为stream ID，不能当作服务消息ID |
| `ip_protocol`, `sequence` | 实际协议号，数据偏移或ACK/NACK累计序号；无可用序号为-1 |
| `model_bytes`, `data_payload_bytes` | 模型包总字节与数据载荷字节，控制包载荷按0记录 |

`all_switch_reason_counts`依次给出四种原因的全程次数。`observations`计选中范围，`events`保存有界明细，`omitted_events`为未保存条数，`complete`只说明选中范围是否完整。运行摘要可以显示截断；公共成功核验要求明细完整，否则明确拒绝。读取器检查格式、端口、时间和计数一致性，不能代替独立逐包守恒核算。

`packet_uid`是包来源身份，不能单独作为一次发送的全局唯一编号。固定依赖的出口准入始终通过，原因2目前没有实际拒绝路径。无路由/链路关闭原先已有设备QbbDrop，新增原因记录可能描述同一次事件；不能将两者相加。物理接收错误、设备队列丢弃和人工故障也分别记录。

## 独立小例

从仓库根目录运行，不需要真实数据账户或历史结果。`COSIM_PROBE`、`COSIM_SERVICE`、`COSIM_PROTOC`分别填写本机已构建的原生probe、service和protoc路径。

```bash
PYTHONPATH=python python3 examples/scaleout_switch_drops/prepare.py --output runs/drop-inputs
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config runs/drop-inputs/topology.json --output runs/drop-fabric
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-probe \
  --topology runs/drop-fabric --config runs/drop-inputs/on.json \
  --messages runs/drop-inputs/messages.json --backend "$COSIM_PROBE" \
  --output runs/drop-on
```

**这是失败用例，最后一条命令预期返回1。** 7个源同时发送262144B到一个目标，接入100G、上层25G、交换机缓冲1MiB、9000B分包；GBN初始RTO40µs、最大160µs、4次无进展重试。保存的原生 `result.json`显示入口拒绝和重试耗尽，不能把失败写成业务完成。`off.json`是相同输入关闭观察的对照，可另存 `runs/drop-off`；不要覆盖已有运行或增加预算改变该固定用例结果。

离线读取失败结果，无需重跑网络：

```bash
PYTHONPATH=python python3 - <<'PY'
import json
from pathlib import Path
from moe_ub_cosim.scaleout_switch_drops import analyze
read = lambda p: json.loads(Path(p).read_text())
result = read('runs/drop-on/result.json')
print(result['passed'], result.get('failure'))
print(analyze(result, read('runs/drop-fabric/topology.json'),
              read('runs/drop-inputs/on.json'), require_complete=True))
PY
```

生成器还提供四卡合成路由的持久SR服务协议，可经过真实LLMServingSim/ASTRA调度：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim scaleout topology \
  --config examples/astra_stream_service/topology.json --output runs/drop-service-fabric
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-run \
  --config examples/astra_stream_service/service.json --topology runs/drop-service-fabric \
  --protocol runs/drop-inputs/service-rdma.json --backend "$COSIM_SERVICE" \
  --protoc "$COSIM_PROTOC" --output runs/drop-service
PYTHONPATH=python python3 -m moe_ub_cosim scaleout rdma-check --run runs/drop-service
```

每个native条件限60秒、RSS1GiB、地址空间2GiB、模拟5ms、产物64MiB、结果16MiB；包和丢弃明细分别最多100000条。开始时须保留8GiB磁盘空间。缓冲、传播和ECN配置为功能参考，未经实机校准；没有仿真实际张量或测量GPU计算。

逐包核算、失败保留与验收范围见[功能证据](../../fixtures/switch_drop_capture/README.md)。
