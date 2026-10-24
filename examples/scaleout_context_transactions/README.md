# 将通信上下文绑定到实际网络连接

这个小例检查多个通信上下文是否错误地共享连接，以及数据是否从声明的网卡收发。它把显式通信域（communicator）、源逻辑rank、context index和peer身份，编译成既有MRT1持久事务连接，再核对同一次原生ns-3执行的源包和端点捕获。

输入有8个物理端点、2个网络平面，实际操作只涉及节点0和4；`gpu_type=NONE`表明不执行或估算GPU计算。4个binding分别代表同通信域的context 0/1/2，以及第二通信域的context 0。其中3个连接共用NIC 1，另一个使用NIC 2；共用NIC不等于共用连接。第一个binding在20微秒声明的本地阶段后再次执行原子加法和写入，不能为每个操作重新建立连接。

## 运行

需要仓库Python环境及现有`transaction_probe`原生程序，构建方法见[远端事务](../scaleout_rdma_transactions/README.md)。`CONTEXT_BACKEND`指向实际可执行文件；此例不自动构建或下载依赖。所有输出目录必须尚不存在。

```bash
export PYTHONPATH=python:.
export PYTHONDONTWRITEBYTECODE=1
CONTEXT_BACKEND=/absolute/path/to/transaction-probe

python3 -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_context_transactions/topology.json --output runs/context-topology
python3 -B -m moe_ub_cosim scaleout rdma-context-plan \
  --config examples/scaleout_context_transactions/program.json \
  --topology runs/context-topology --protocol examples/scaleout_context_transactions/rdma.json \
  --output runs/context-plan
python3 -B -m moe_ub_cosim scaleout rdma-transaction-probe \
  --topology runs/context-plan --protocol runs/context-plan/protocol.json \
  --config runs/context-plan/transactions.json --backend "$CONTEXT_BACKEND" --output runs/context-run
python3 -B -m moe_ub_cosim scaleout rdma-context-check \
  --plan runs/context-plan --run runs/context-run --output runs/context-check.json
```

换成`drop-first-rdma.json`并采用新的plan/run/check路径，可测试首个请求包在节点4的NIC 1真实丢失一次。它沿用相同程序和80微秒初始超时，不人为补写接收或完成记录。

## 修改输入

`program.json`格式为`moe-rdma-context-program/1`。`communicators`声明名称、`requested_contexts`、每rank的GIN connection数`connections`，以及逻辑rank到物理node和本地NIC表的映射。逻辑rank必须从0连续编号；同通信域不能让两个rank映射到同一物理节点。

context按connection数向上取整分配。对于context index `i`和connection数`C`，`gin_connection=i%C`，`local_context=i//C`，物理NIC取本地表的`gin_connection % len(nics)`项。向上取整多出的context index也合法。例如请求3个、C=2实际分配4个；这只是显式连接规则，不自动选择最优通信参数。

`bindings`指定名称、通信域、源/peer逻辑rank、context index、plane、请求/回复端口和队列深度。相同持久身份必须复用binding名称，另起名称重复声明会拒绝；相同NIC可承载不同binding。plane必须与两端物理NIC可达性一致。MRT1需要请求和反向响应两个载体，不等于两个真实GIN QP。

`program`沿用transactions/5，但省略`connections`；每个remote操作通过字符串`binding`选择连接。地址、权限、依赖、重叠副作用保护及原有限额保持，详见[事务输入](../scaleout_rdma_transactions/README.md)。当前限定最多32物理GPU、64个binding、1024个远端操作，不因增加context而放宽。

## 输出与限制

plan目录保存`input.json/topology.json/protocol.json/transactions.json/bindings.json`及SHA-256清单。它只证明静态规划成功，`native_network_executed=false`。运行仍通过现有公开事务入口，全部连接共享同一个网络时钟和交换机状态。

核查命令验证计划派生、运行输入角色及hash、远端和本地完成、每载体只创建一次QP、实际源TX及目标RX的NIC/端口、目标字节范围覆盖、完整ACK和物理排空。须开启覆盖全部端点的完整`packet_capture`及`wire_headers`。核查输出每binding的操作数、两个载体的源包数、RX数和字节数；重传字节计入实际源payload。它不替代独立目标内存/原子返回核验，见[验收](../../fixtures/context_transactions/README.md)。

self-peer明确拒绝，不能从现代计划删掉必要self操作后声称完整接入。此入口不提供真实GIN报文、provider原子服务、现代DeepEP执行、GPU可见性、QoS成本或硬件校准；小例完成时间只能解释MRT1功能模型。ELF可能嵌入绝对库路径，跨目录运行须检查`loaded_libraries`，不能仅以设置`LD_LIBRARY_PATH`认定加载了新库。
