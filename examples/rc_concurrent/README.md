# 多条可靠连接共享网络的小例

四条 RDMA 可靠连接（RC，每条由队列对 QP 标识）共用一个原生 ns-3 网络。两条共用源网卡，三条修改同一目标计数器；各自保存独立的包序号、重放缓存和定时器。用于检查竞争和丢包时的正确性，不执行模型计算或推理请求。

在仓库根目录运行。先准备输入，再复用一次完整、版本一致的公开 `scaleout rdma-build` 产物，只编译独立 CPU 驱动：

```bash
PYTHONPATH=python python3 -B examples/rc_concurrent/prepare.py --output /tmp/rc-input
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rc-fabric-build \
  --runtime /path/to/complete-rdma-build \
  --identity /path/to/complete-rdma-build/backend.json --output /tmp/rc-driver
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rc-fabric-probe \
  --topology /tmp/rc-input/topology --protocol /tmp/rc-input/rdma.json \
  --config /tmp/rc-input/healthy.json \
  --backend /tmp/rc-driver/moe-rdma-rc-concurrent --output /tmp/rc-healthy
```

输出路径须尚不存在，父目录须存在，所在卷保留至少 8 GiB 空闲。完整构建方法见[单连接示例](../rc_packet_probe/README.md)。新构建的 `backend.json` 包含 `native_commit`、八个 `library_hashes` 和 `fabric_archive_sha256`。旧构建可传入有固定库哈希的独立身份记录；缺少静态库历史哈希时记录为本次接纳的预编译库，不称独立重建证明。构建核对锁定版本、旧 Fabric 源码、有效选项及实际加载库，不重建 ns-3 或修改原构建。

默认网络为 4 台服务器各 1 端点，经 ToR、汇聚和共享核心相连，单 rail，所有链路 100 Gb/s、1000 ns。GPU 类型 NONE 只表示网络端点。前三条连接为 0→3、1→3、0→3，各先写 600 B 至互不重叠区，再对偏移 3072 的共享计数器加 1、2、3，初值 41。第四条为 3→0，写 32 B 后将另一计数器从 7 加至 11。

另生成 `drop_write_middle.json`、`drop_atomic_ack.json`，分别只丢连接 0 的中间写入包、连接 1 的原子回复一次。替换 `--config` 并选新输出目录即可运行。源收到自身 QP 的实际 ACK 或 Atomic ACK 才完成，后者携带原子执行前的旧值。共享计数器最终应为 47，各连接取得的旧值由实际收包顺序决定，不规定跨 QP 的先后。

输入格式 `moe-rc-concurrent-input/1`：

| 字段 | 含义和限制 |
|---|---|
| `memories` | 2～8 个 GPU 注册区，每区 8 B～64 KiB，总计最多 256 KiB；`data_hex` 为小写十六进制 |
| `connections` | 2～8 条，`id` 在 0～7 内唯一；指定 `src/dst/source_nic/destination_nic/plane` |
| `operations` | 顺序 `write/fetch_add`，全部连接合计最多 64 项、64 KiB 写入；地址为目标字节偏移，64 位原子须 8 B 对齐，内存小端 |
| `mtu` | 256/512/1024/2048/4096 B，与协议 `payload_bytes` 一致 |
| `rto_ns` | 当前操作最后一个请求包发出后计时，最多三次重试，不超过仿真预算的 1/5 |
| `loss` | `null` 或 `{connection,node,opcode,psn}`，限定连接和 GPU 物理接收位置，只丢一次 |

源/目标 QPN 为 `2*id+1`、`2*id+2`；请求/回复 UDP 源端口为 `12000+id`、`13000+id`，目的端口 4791。这些是协议字段，任务标识不上线。每连接一次仅一项工作队列元素（WQE）在途；序号 NAK 会返回，但在固定 RTO 时整体重发当前操作的请求包。

`result.json.rc_concurrent` 保存每连接完成时刻、原子旧值、超时次数、最终内存、目标效应、端点事件及逐跳包字节。时间单位 ns，源完成、目标生效和网络排空分别解释。Fabric 同时保存端口字节、队列占用及面积。见[独立核验与结果](../../fixtures/rc_concurrent_fabric/README.md)。

仅使用一套固定原生库，保留物理 NIC 调度、交换队列、MMU、PFC 和 ECMP；固定发送速率，未接入 RC 拥塞控制。ICRC 为四字节零占位，包长含 PPP 而非完整以太网开销。未覆盖 QP 建立、READ/SEND/RNR、PSN 回绕、多项在途 WQE、GPU 内存序或硬件校准。旧 MRT1/modern/UB 未迁移到此路径，本例未调用 LLMServingSim。

单编译最多 120 秒、RSS 2 GiB、地址 4 GiB、产物 64 MiB；单运行最多 60 秒、RSS 1 GiB、地址 2 GiB、产物 64 MiB。示例另限制仿真 5 ms、捕获 80000 条。失败保留结果，不自动提额或重跑。
