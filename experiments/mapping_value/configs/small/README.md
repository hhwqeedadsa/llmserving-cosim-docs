# 固定小场景输入

本目录保存独立实验输入，不在执行时读取 `fixtures/`、测试驱动或历史运行。格式使用平台已有 JSON/YAML，修改后通过公开 `resolve` 再次校验。操作命令见[实验用法](../../usage.md)。

| 输入 | 选择 |
|---|---|
| 模型与请求 `service.json` | 8 rank；每 rank 4 请求；4 块，其中 1 dense、3 MoE；隐藏维度 1024；16 路由专家、Top-2、每 rank 两个；本地共享专家一份 |
| 初态与轮次 | 全部在 0 ns 就绪，prompt 长度 64，KV 与首个生成 Token 已就绪；每请求一份 Decode 路由，固定批次推进一轮 |
| 路由生成 | 对每个 request=0…31、layer=1…3，将 expert=0…15 按字符串 `mapping-value/seed=0/request={request}/layer={layer}/expert={expert}` 的 SHA-256 原始字节升序排列，取前两个；结果直接保存在服务文件中，不强制均衡 |
| 模型存储 | MLA latent 32、RoPE 16、16 头；压缩 KV 为 BF16，矩阵 INT8 加每输出通道 FP32 scale；其余存储及参考成本显式保存于服务文件 |
| 容量 | 每 rank 权重及 scale 为 12,909,248 B；工作区 1 GiB；通信预留 64 MiB；1024 个 KV 块，每块 16 Token、6144 B；设备容量按这些项目求和为 1,160,051,392 B |
| 通信 `layers.json` | 原生 STORE Dispatch、LOAD Combine；32 B 供数通知；8192 B 向量分块；1 发送槽；2 条逻辑 AIV 序列；额外端侧准备成本全零 |
| 物理网络 `fabric.json` | 2 服务器×2 芯片×2 die；两平面、每平面两台 L2；8 DEVICE、4 L1、4 L2，24 条双向链路，32 个交换出口 |
| 后端 `scenario.yaml` | 每方向 224 Gb/s、传播 20 ns、节点仲裁 1 ns、输入处理 0；原生 worker=1、STORE/LOAD 在途各 32、请求编号 256、载荷 4096 B；最短路喷洒，CBFC，seed/run=1；无出口服务干预 |
| 观测 `observation.json` | 全部 32 个交换出口，100 ns 窗口、[0,1 ms)；详细事件、队列和阶段开启；保留五类应用来源；所有时钟无偏移、无漂移 |
| 实验选择 `experiment.json` | 锚点 rank 0；六个增强观测出口；100 ns 操作变化容差；整轮实质影响阈值为 max(100 ns, 正常整轮时间×0.001) |

设备容量是缩小模型的实验预算，不代表某款硬件的 HBM。计算成本仍为声明参考值，未根据矩阵大小或目标 NPU 测量校准。通信、存储和网络参数分别核算；相同工作量的成对运行只修改冻结干预。

完整场景保持独立配置和来源，不能把本输入称为缩小 rank 的原始 R1 部署。
