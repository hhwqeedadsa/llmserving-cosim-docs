# 共享上行断链：重传与现成改路方法的对照

本轮回答：相同真实MoE输入下，交换机间链路失效后，仅依靠端侧选择性重传是否足够？本地剔除坏出口能够收回多少等待，又有哪些方向必须等待全局改路？这是成熟网络机制的收益边界，不将重传、ECMP或本地故障响应称为创新。

## 执行前固定的条件

- 128端点、20交换机、单plane Clos，四个交错EP32组。15份不同R1-AWQ/MMLU原件各一请求，各组4/4/4/3；两层、两个单token Decode步。batch_per_rank=1，计算是声明值，payload是功能标记；不是完整671B推理、Prefill或128卡满载。
- 沿用17µs初始RTO、640µs上限、3次重试、SR和参考DCQCN；无helper/备用，原ACK、集合门限及字节buffer释放条件保留。
- 故障位置按拓扑排序固定为第一条ToR–Agg边，不按故障结果或热点强度挑选；当前为128–132，400Gb/s。仿真4µs关闭两个物理端口，在途包与已排队包按后端真实处理。
- **当前功能是永久断链，暂态的是路由收敛期间的丢失，不是20/60µs后链路重新上线。** 与此前GPU32的短时DATA黑洞属于不同故障，不能直接互换结论。ACK也可能经过坏链路，不再人为保留所有控制包。
- 对照五个新网络：`global20/global60`在断链20/60µs后全局重建路由；`local20/local60`额外启用`live_ecmp/1`，立即从本地旧等价候选剔除坏出口；`global0`在断链时立即全局重建，作为理想控制响应参考。local没有额外检测/编程耗时，不是生产FRR性能。
- 正常对照只读复用已验收的十五请求17µs运行，准备时核对原件、全部图、服务参数和后端身份。五个新网络只各运行一次，不重复正常网络，不按结果改变故障位置/时间/负载或超时。

本轮准备前已经知道上一轮正常/故障结果，属于机制扩展，不宣称盲测。正常链路实际承载量用于解释共享程度，不用于事后选更坏的链路。

## 竞争解释与判别

1. 若旧等价出口可用，本地剔除可减少故障到全局更新之间的坏端口丢失；应能看到同一源流改走存活出口，源序号/累计ACK不重置。
2. 对于旧表只有故障下行出口的目的，本地剔除不能生成新绕路；空候选仍真实丢弃，可能必须等全局更新。不能把local表的候选计数当实际改路包数。
3. 较早转发可能把同一批数据移到其他共享端口，造成组间代价。分别报告四组完成、网络排空、源重传字节、物理/本地丢失、队列及buffer峰值，不能只挑最快组。
4. 即时全局改路也保留断链瞬间的丢包和减少后的容量；它是实际执行的理想响应条件，不是零故障上界、可部署收益或事后择优策略。

## 预算与复现

独立根`runs/shared_uplink_recovery_20261030`：累计1200受监控秒、新增2GiB、卷预留8GiB；每CLI120秒/RSS1GiB/地址2GiB。native沿用60秒、模拟5ms、包/队列200000行、每次产物512MiB/结果256MiB。最多五个新128Clos网络；零构建/下载/安装/依赖变更/GPU。失败保留，不扩限或自动重跑。

用`.venv/frontend/bin/python -B`执行下列入口；`monitor.py`记录真实命令、退出状态和资源，输出目录必须不存在：

```bash
export PYTHONPATH=python
python experiments/shared_uplink_recovery/monitor.py --root /path/to/new-root --stage prepare -- \
  python experiments/shared_uplink_recovery/study.py prepare \
  --source-manifest /path/to/source-manifest.json --source-base /path/to/repo \
  --build /path/to/verified-build --normal /path/to/previous-transfer-normal --output /path/to/new-root/study
python experiments/shared_uplink_recovery/monitor.py --root /path/to/new-root --stage run-global20 -- \
  python experiments/shared_uplink_recovery/study.py run --study /path/to/new-root/study \
  --case global20 --protoc /path/to/protoc
```

其他case同理。全部结束后`study.py analyze --study ... --output ...`，再用独立`verify.py --study ... --delivery ... --output ...`核对原始记录。分析程序不参与在线控制；预注册脚本和输入hash冻结后不得按结果修改。新增展示脚本单独记录身份。原始路由文件不提交。

当前断链/局部改路实现依据：[链路故障](../../examples/scaleout_link_failure/README.md)、[local ECMP](../../examples/scaleout_live_ecmp/README.md)、`backend/rdma/fabric.cc`与固定原生`QbbNetDevice::TakeDown`。物理恢复、显式控制报文与硬件校准仍是缺项。
