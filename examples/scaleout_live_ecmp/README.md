# 交换机避开本地已失效的等价出口

本例用于检查Scale-Out交换机在全局路由更新之前，是否能利用自己端口的实际状态继续转发。协议可显式添加`"local_failover": {"mode": "live_ecmp/1"}`，并必须同时配置`link_failures`；省略该字段时保持原行为。

8个GPU标签、10个交换机组成单plane Clos，下层100Gb/s、上层25Gb/s。4,000ns关闭8—10，16,000ns才全局改路。两条持久连接各发布两次16,000B，后两次等待6,000ns本地工作，后继继续等待实际接收/ACK。GPU类型为NONE，本地耗时为声明值，无GPU执行。这是人工功能输入，不是完整MoE推理或研究性能对照。

## 运行

按[原生RDMA构建](../scaleout_rdma/README.md)取得支持本字段的当前后端，将`COSIM_STREAM_PROBE`设置为`backend.json`中`stream_probe`的实际绝对路径。以下输出目录必须不存在，父卷预留8GiB；不需要运行GPU或下载模型。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_live_ecmp/topology.json --output /dev/shm/live-topology
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /dev/shm/live-topology --protocol examples/scaleout_live_ecmp/protocol.json \
  --config examples/scaleout_live_ecmp/work.json \
  --backend "$COSIM_STREAM_PROBE" --output /dev/shm/live-result
```

`work-empty-route.json`仅更换反向连接的线上端口，使原生哈希选路经过失效边上方、原先只有一个下跳的汇聚交换机。可用新的输出目录替换工作输入，检查“旧候选全部失效仍真实丢包”。不要将两输入的完成时延差称为优化收益：线上端口变化也改变了路径。

## 行为与输出

实际关闭端口后，仅故障边两端从原全局表中剔除本地坏端口，保留候选顺序及原生ECMP哈希。没有活候选的目的暂时无路由，仍会丢包。其他交换机仍使用原表，源仍依实际ACK、反馈和超时恢复；在途包、已排队包及已丢包不会被取消或迁移。交错故障的全局更新只排除届时已经收敛的故障；各交换机继续过滤自己的坏端口，不提前把其他故障通知远端。

`result.json.link_failures.local_failover`记录模式、范围及每次故障两端的候选数、安装数、空目的数和被剔除端口。候选计数不是包计数。完整捕获中的本地转发丢弃绑定入口端口，需结合实际包头、接收及当时路由判断原因。字段见[输出说明](../../docs/record_storage.md)，独立包/哈希验证见[功能验收](../../fixtures/live_ecmp/README.md)。

该模式没有额外本地检测或编程时延，是立即本地响应的经典理想参考。不支持新绕行、显式源端故障通知、链路恢复或生产FRR/LFA成本，不代表现代GIN、硬件校准或论文创新。
