# WRITE与Atomic ACK经过同一网络的小例

这个例子检查两个服务器间的RDMA可靠连接（RC）操作包。源先写入600字节，再把目标区的64位计数器从41加到46；携带旧值41的Atomic ACK实际到达源端后，原子操作才完成。它是网络功能参考，不是完整MoE任务或网卡性能模型。

使用已有构建工具及Protobuf安装，不安装依赖：

```bash
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-build \
  --output /tmp/rc-build --cmake /path/to/cmake --ninja /path/to/ninja \
  --protobuf-prefix /path/to/protobuf --compile-wall-seconds 900
PYTHONPATH=python python3 -B examples/rc_packet_probe/prepare.py --output /tmp/rc-input
PYTHONPATH=python python3 -B -m moe_ub_cosim scaleout rdma-rc-probe \
  --topology /tmp/rc-input/topology --protocol /tmp/rc-input/rdma.json \
  --config /tmp/rc-input/healthy.json --backend /path/from/backend-json/rc_probe \
  --output /tmp/rc-healthy
```

所有输出路径必须尚不存在；构建和运行所在卷至少保留8GiB。构建JSON和`backend.json`中的`rc_probe`是实际可执行文件路径，不能把另一个probe替代它。配置路径均由调用者明确传入；不会从当前目录寻找旧运行。

`prepare.py`只生成拓扑和输入，不运行网络。网络为2服务器各1GPU、单rail、两组ToR/汇聚交换机及1个共享核心，每链路100Gb/s和1000ns。GPU类型为NONE，仅代表通信端点，不包含计算或HBM模拟。四份输入分别是正常、丢一次中间WRITE包、丢一次最终WRITE ACK、丢一次Atomic ACK；可在上述命令中替换`--config`，每次选择新的输出目录。

修改`operations`可增加非空`write`或对齐64位`fetch_add`；地址是声明注册区的字节偏移，目标内存显式小端。`data_hex`、`memory_hex`只接受小写十六进制；`addend`为无符号64位数。最多64操作、64KiB总WRITE及64KiB目标区，单QP每次仅一项操作在途。`mtu`只接受256/512/1024/2048/4096，并与protocol的`payload_bytes`相同。`source_nic`和`destination_nic`是生成拓扑中的设备接口编号，本例均为1；`plane`为0。

`rto_ns`按当前WQE最后一个请求包实际发送的时间启动，超时重发当前WQE，最多3次；序号NAK会记录，但本参考在RTO时恢复。操作完成后取消计时。`loss`为null或`{node,opcode,psn}`，在所选GPU的物理接收错误模型丢一次匹配包；opcode为BTH数字操作码，不能套用旧字节偏移或legacy ACK协议号。固定小例为PSN1/opcode7、PSN2/opcode17、PSN3/opcode18。

`result.json`同时含实际Fabric端口/队列统计及`rc_reference`：目标效应、最终内存、源完成、请求/响应记录和逐物理跳的包字节。所有时间为同一ns-3时钟的纳秒。各条件由自己的`inputs.json`及`execution/probe.json`保存输入hash、命令、退出码和宿主资源；运行上限60秒、RSS1GiB、地址2GiB、产物64MiB。目标效应和源完成应分别解释，不能将最后排空时间直接当推理时延。

包使用IPv4/UDP4791、BTH及实际所需的操作扩展；模拟器work/task标识不上线，响应无需再被ACK。请求优先级3，反馈高优先级0。使用原生物理NIC固定速率调度、交换机队列/MMU/PFC和ECMP；调度容器的计数单位是包票据，独立于RC的PSN，不代表真实网卡窗口或信用。不使用legacy DCQCN反馈，也不支持INT修改模式、SEND/READ/RNR、QP建立、PSN回绕、多个在途操作、GPU内存序或真实NIC耗时。

ICRC仅保留4字节零占位，不计算或验证；IPv4 UDP校验和为0。链路字节含2字节PPP，未建模完整以太网帧开销，因此不能声称可直接与RNIC互通。独立Python状态参考及`atomic_return_required/1`安全边界见[操作状态小例](../rc_operation_reference/README.md)。现有MRT1/modern/UB入口保持各自语义，本命令不替代这些入口。
