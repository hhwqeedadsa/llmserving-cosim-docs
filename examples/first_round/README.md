# 完整首轮小例

本例用于熟悉从服务输入到研究观测的完整流程，也用于检查重新采样和后端重放是否可用。输入全部保存在此目录，无需模型权重、GPU 或外部路由数据。安装与运行步骤统一放在[用户指南](../../docs/user_guide.md)。

## 模拟什么

三个 rank 分担一个小型混合专家（MoE）模型。rank 是分布式任务中的逻辑参与者，本例编号为 2、7、12；每个 rank 在每个 MoE 块上承载两个专家。

| 项目 | 配置 |
|---|---|
| 模型 | 隐藏维度 1024，一个稠密块、三个 MoE 块，每块六个专家，每个词元（token）选择两个专家 |
| 请求 | 三个请求同时到达；201、202 在 rank 2，204 在 rank 7，各执行一次解码迭代 |
| 解码初态 | 各请求的注意力键值缓存（KV 缓存）已就绪，本例不运行提示词预处理（Prefill） |
| rank 12 的作用 | 没有本地请求，仍为其他 rank 处理专家输入并返回结果 |
| 部署 | rank 2 与 7 位于同一芯片，rank 12 位于另一芯片 |
| 网络 | 跨芯片通信进入 ns-3-UB；同芯片通信按本地依赖和配置耗时处理，不生成网络包 |
| 观测 | 三个 rank 的端侧操作，以及交换机 8 的端口 0、4 在 `[0, 1000000)` ns 内的统计，原采样周期为 1 µs |

模型包含解码所需的计算与通信依赖，计算节点使用声明的参考耗时，不执行张量数值运算。专家选择是人工构造的合成数据。这些输入用于功能验证，不能据此估计真实设备的推理延迟。

## 各输入文件负责什么

| 文件 | 内容 |
|---|---|
| [run.json](run.json) | 运行入口：引用其他输入，设置后端、仿真时限和宿主资源预算 |
| [service.json](service.json) | 模型结构、三个请求的专家选择、组批限制、显存及参考计算耗时 |
| [layers.json](layers.json) | 各层 rank 与网络节点映射、专家放置和依据 CANN 源码抽象的通信模板参数；其中占位 token 由实际请求替换 |
| [system.json](system.json)、[network/](network/) | ASTRA 任务执行器设置与 ns-3-UB 网络配置 |
| [observation.json](observation.json) | 设备时钟、观测端口、时间范围和原 1 µs 周期 |
| [record-policy.json](record-policy.json)、[export-policy.json](export-policy.json) | 原始记录与研究结果的存储预算 |
| [sample.json](sample.json) | 继承已保存首轮的观测设置，只用 `period_ns` 将周期改为 10 µs |
| [sample-10us.json](sample-10us.json) | 相同 10 µs 观测的完整写法，可作为单独编写观测 JSON 的参考 |
| [replay.json](replay.json) | 用保存的任务重新运行后端，沿用原网络与观测 |

配置内路径相对该配置文件所在目录。后端默认来自仓库的 `build/default/`；其他构建位置通过 `--backend` 指定。输入中的 `max_num_seqs` 等调度限制按 rank 生效，所以两个本地请求可在 rank 2 同时进入这轮解码。

## 应得到什么

按用户指南执行后，`runs/demo-run/observed/` 保存端侧操作时间和端口采样统计；`truth/` 保存供评价使用的实际对应关系。它们的字段与编码见[记录格式](../../docs/record_storage.md)。研究算法只读取 `observed/`。

保存首轮后，`runs/demo-10us/observed/` 使用同一执行事件生成 10 µs 观测；`runs/demo-replay/` 则保存后端重新执行的结果。这两种操作的区别、成功状态检查及输出目录要求见[用户指南](../../docs/user_guide.md#保存与复用首轮)。
