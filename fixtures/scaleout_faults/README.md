# 原生RDMA故障与被动包记录验收

输入由[公开命令检查](../../tests/integration/check_rdma_faults.py)独立生成，不依赖旧运行。执行方式：

```bash
python tests/integration/check_rdma_faults.py --backend-json <新原生构建>/backend.json \
  --protoc <既有protoc> --output <不存在的目录>
```

本轮完成11个探针（正常、完整采集、中间包恢复、尾包停滞、早期ACK覆盖、最终ACK停滞、NACK丢失、重复丢弃、入口交换机首包恢复、采集截断、窗口零命中）和3个实际LLMServingSim服务运行（无故障、恢复、最终ACK停滞）。失败是预先声明的非零退出，须有未完成状态；诊断命令成功不改变原仿真失败结论。

独立检查关闭/开启/截断采集时的消息、端口、路径、回调和队列语义完全相同。大消息身份50000按完整端点关联，不能仅以取模端口命中；事件完整性和零命中显式导出。真实服务核验唯一业务字节、含重传流量、接收连续字节和独立SEND/RECV回调；首个尝试在交换机丢弃时保留其真实部分路径。

[evidence.json](evidence.json)保存结果、实际检查入口及源码摘要。完整输入、命令、成功与失败记录、构建来源及资源日志归档于本机`runs/scaleout_faults_20261021/evidence.tar.gz`，清单见同目录`archive.json`。共享内存目录不是持久备份。

另外检查已有UB发送状态的Python输入/导出与原C++记录器、精确形状profile的原有单元检查；这些仅是相应模块回归，不宣称重新验证完整Scale-Up网络或硬件精度。故障研究单独见[实验说明](../../experiments/scaleout_faults/README.md)，不把本功能门当成策略收益验证。
