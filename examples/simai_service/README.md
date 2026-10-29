# SimAI调度与原生ns-3联合小例

本例执行固定Alibaba SimAI的Vidur分支真实Cluster、Orca调度器和Request/Batch生命周期。使用64个物理端点、EP64，每rank每层4个专家；仅rank0/32各有一个人工请求。两步Decode、一个MoE层，源JSON为明确的人工功能输入，不是实测路由。采用EP64是为了让上游完整DeepSeek模型容量参考合法，并不执行完整671B模型。

先按[来源准备](../simai_frontend/README.md)生成runtime；使用已有的Python `simai`依赖环境、`protoc`和原生`rdma-build`生成的`service`程序。以下从仓库根执行，`$SIMAI_RUNTIME`、`$NATIVE_SERVICE`是对应目录和程序的路径；不得使用UB后端。

```bash
python -m moe_ub_cosim scaleout topology --config examples/simai_service/topology.json --output runs/simai-example/topology
python -m moe_ub_cosim scaleout simai-run --config examples/simai_service/service.json --topology runs/simai-example/topology --protocol examples/simai_service/rdma.json --runtime "$SIMAI_RUNTIME" --backend "$NATIVE_SERVICE" --output runs/simai-example/normal
python -m moe_ub_cosim scaleout simai-check --run runs/simai-example/normal
python -m moe_ub_cosim scaleout rdma-arrivals --run runs/simai-example/normal --graph g0.w0 --layer 0 --phase dispatch --output runs/simai-example/normal-arrivals
```

`--protocol examples/simai_service/rdma-loss.json`可另存一份故障运行：消息7的数据offset9000丢两次、消息14的最终ACK丢一次。两者采用相同显式Go-Back-N恢复配置；不是选择性重传或新的恢复算法。

输入`moe-simai-service/1`包含`service/simai/routes`。`service`声明有限层段、EP组和计算成本；`simai`选择上游`deepseek-671B`容量参考、A100/H100配置和最大序列长度；`routes`保存原件hash、原始层号、Decode entry、源rank和slot。当前仅支持R1形状H7168/F2048/E256/Top8，激活精度由`activation_bytes`声明；不支持克隆文件补请求或以随机选择替代原件。不同EP组必须划分全部物理GPU，每组至少一个请求；每rank请求数不超过batch上限。源文件相对配置解析，运行时复制为可搬迁输入。

没有源token的rank不承担虚假的Attention、pack、merge，但仍执行实际收到的专家计算。上游原生Cluster建立EP属性，MemoryPlanner检查容量；初始化为已具备KV的观察窗口，没有运行Prefill。每轮Chakra图包含计算与SEND/RECV依赖；共享一个原生ns-3网络，完成事件再推进上游Batch和下一轮调度。计算值是声明参考，GPU型号不代表实机profile校准。

实际选批后的消息在导出图之前，与LLMServingSim入口共用[消息额度及身份预检](../scaleout_rdma/README.md#修改哪些参数)。结构预检通过不代表未来每条消息都能准入；运行不自动改变batch、额度或端口来绕过失败。

输出`frontend.json`保留真实调度来源、请求进度、批次和完成反馈；`graphs/*/wave.json`保留源索引和消息矩阵；`result.json`保留实际包、收发、恢复和回调；`verification.json`独立从原件重建字节/依赖并核对完成。`execution/`记录真实命令和宿主资源，不是GPU时间。逐包CSV、双端统计和累计到达由`rdma-arrivals`导出；有效接受不等于GPU内存可见。

首次功能验收见[固定摘要](../../fixtures/simai_service/README.md)。
