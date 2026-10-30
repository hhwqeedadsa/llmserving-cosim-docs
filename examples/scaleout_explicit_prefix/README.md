# 执行显式修复段

本例检查原ECMP候选全失效时，交换机能否将包经安全中间节点送回正常路由。使用128端点Clos、4条双向人工持久字节流和选择性重传，无模型、GPU或真实路由账户依赖；固定端口为功能覆盖选择，不代表MoE负载或128卡满载。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B examples/scaleout_explicit_prefix/prepare.py \
  --output /path/to/new-input
.venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-build \
  --output /path/to/new-build --compile-wall-seconds 600 \
  --cmake /path/to/cmake --ninja /path/to/ninja --protobuf-prefix /path/to/existing-protobuf
.venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-stream-probe \
  --topology /path/to/new-input/topology --protocol /path/to/new-input/prefix.json \
  --config /path/to/new-input/work.json --backend /path/to/new-build/output/moe-rdma/ns3.42-moe-rdma-stream-probe \
  --output /path/to/new-result
```

后端可执行文件以构建输出`backend.json`中的`stream_probe`为准；不同ns-3版本命名可能带前后缀。所有输出目录必须不存在。编译工具/Protobuf须已安装，命令不安装依赖。协议及work各自包含模拟时间、包/队列记录、RSS及产物预算；GPU类型标签为NONE，计算只是一段声明的功能延时。

协议中增加`"local_failover": {"mode":"explicit_prefix/1", "max_events":100000}`，必须同时声明一条实际交换机边的`switch_links/1`或`switch_links/2`故障。支持单plane0、每GPU单NIC、128/256/512端点Clos，最多16跳；当前功能验收规模为128。输入生成器同时写出normal、live、prefix、restore、instant五个条件；normal无故障，live只过滤旧候选，prefix封装显式段，restore在12µs恢复原端口，instant立即全局更新。它们用于正确性核查，不自动代表研究收益。

计划在完整旧图上预先计算，只在故障两端的旧候选全部不可用时激活；有存活候选仍用原顺序/哈希。中间节点从实际线上头部读取修复段，不提前获知远端故障。例中保护128–132边，Agg132向GPU1的修复段为132→129→133，之后按旧路由133→128→1继续。物理恢复或全局通知会重新安装本端候选，已经在途的修复包仍按所携带的段执行。

封装为项目自定义IPv4协议250，**不是IANA分配、标准RoCE、MPLS或TI-LFA实现**。原包完整保留，外层包括原生2B PPP、20B IPv4、8B MRP1版本/游标/合流信息及每跳4B节点/端口；两跳段每个在线包额外38B，计入真实队列/MMU和序列化。合流时拆封，外层CE合并到内层。DATA仍走原优先级，ACK/NACK/CNP仍走原控制队列；原生控制队列本来不计数据MMU。没有另建封装处理、检测、路径分发、MTU碎片或控制报文的硬件模型。

`result.json.explicit_repair`保存封装/转发/合流意图、完整性和与逐包记录一一对应的外层字节/协议。`packet_capture`继续报告内层流身份、协议及有效payload，而`model_bytes`始终是实际在线包大小，不能把新增38B算成业务数据。意图记录需与实际TX/RX核对，不能单独证明送达。现有ACK/SACK、重传、门限和QP寿命保持；接收到达不等于源确认或资源退出。功能核验见[验收目录](../../fixtures/explicit_prefix/README.md)，规划说明见[静态规划](../scaleout_repair_plan/README.md)。
