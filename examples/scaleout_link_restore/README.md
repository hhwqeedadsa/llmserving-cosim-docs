# 短时断链后恢复同一物理链路

本例生成128个GPU标签、20个交换机的单plane Clos，实际运行4条持久字节连接。GPU类型为NONE，消息是人工功能输入，100µs本地等待也是声明值；它不代表真实MoE流量或性能收益。每连接先发送64,000B，之后在原连接再发送16,000B，源序号、重传和拥塞状态保留。

## 运行

使用[原生RDMA公开构建](../scaleout_rdma/README.md)得到包含本功能的`backend.json`，将其中`stream_probe`的绝对路径填入`COSIM_STREAM_PROBE`。输出目录必须不存在，卷保留8GiB；不需要GPU、真实路由下载或安装依赖。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B examples/scaleout_link_restore/prepare.py --output /path/to/new-input
.venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /path/to/new-input/topology --protocol /path/to/new-input/stale-down.json \
  --config /path/to/new-input/work.json --backend "$COSIM_STREAM_PROBE" --output /path/to/new-run
```

准备程序还生成`ordered-local.json`、`physical-before-report.json`、`inflight.json`和不注入故障的`normal.json`，分别使用新运行目录。它固定线上端口，使原始ECMP经过目标上联，目的是覆盖功能，不是按结果挑选研究收益。

## 输入与实际含义

永久断链仍用`switch_links/1`。临时断链用：

```json
"link_failures": {
  "mode": "switch_links/2",
  "events": [
    {"src": 128, "dst": 132, "time_ns": 4000, "route_update_delay_ns": 296000,
     "restore_time_ns": 24000, "restore_route_update_delay_ns": 0}
  ]
}
```

单位均为ns。关闭在4µs，物理恢复在24µs；断链通知在300µs才到集中控制器，恢复通知在24µs到。控制器先接受版本2的恢复通知，之后忽略旧版本1的断链通知，最终不会再次排除已经恢复的链路。

1..8条不同的实际交换机间边，每条最多一轮关闭/恢复；src<dst、同plane。恢复时刻必须严格晚于关闭；两次物理事件和两次通知都须早于max_sim_ns；延迟允许0。v1不接受恢复字段，v2要求两个恢复字段同时存在。非法整表在建网前拒绝，不支持NIC或反复flap。

物理恢复不意味着路由立即知晓。`physical-before-report`在10µs物理恢复，但20µs才收到旧断链通知、30µs收到恢复通知；20–30µs控制面仍合法排除该边，不能读取物理真值偷偷跳过。`ordered-local`演示先获知断链、后物理恢复、再获知恢复，额外开启本地坏出口剔除。`inflight`在1,050–1,051ns关闭，覆盖原包尚未发送完成时恢复。

## 完成与边界

关闭调用固定原生TakeDown，真实清空该端口队列并回收计数。恢复使用同一个设备、channel和原发送完成事件，不重新Attach，不重建QP，不补回已经丢弃的包。物理包根据实际到达时的端口状态接收或丢弃；端侧仍依赖真实ACK/SACK和超时重传。

若启用`local_failover.mode=live_ecmp/1`，本地只能重新安装当前全局旧候选中的活端口；全局已排除的边等待恢复通知，空候选也不会自动生成新绕路。该本地动作无额外检测/编程成本。

`result.json.link_failures`记录link_up、版本化routes_updated和routes_update_ignored；原任务已ACK仍须等待未来通知与物理排空。字段见[输出说明](../../docs/record_storage.md)。独立验收及限定范围见[功能验收](../../fixtures/temporary_link_restore/README.md)。

控制面是声明延迟的集中通知模型，没有真实控制报文、分布式路由协议、物理光模块训练或硬件校准。原生接收按到达瞬间端口状态判断，未模拟短暂信号中断对帧中间比特/CRC的影响；1ns窗口仅检查发送状态生命周期，不能推断真实短断链的丢包率。共享Fabric接入各原生入口，当前实际验收为128Clos持久字节例，未执行完整LLMServingSim恢复性能对照；临时断链功能不等价生产RoCE快速恢复，也不构成论文创新。
