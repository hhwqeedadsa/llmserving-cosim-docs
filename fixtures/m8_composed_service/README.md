# M8 多层、多请求、多组联合执行

`service.json` 声明两个独立 EP 组、完整模型容量及七个就绪 KV 请求，`layers.json` 声明各组执行的连续 MoE 层 1、2、3。每轮选中的请求依次经过三层，整段完成后才推进一次 Token；不同组在同一套 UB 网络中独立推进。输入均为合成功能数据，计算和硬件状态仍为 `unvalidated_reference`。

## 输入与执行边界

| 输入 | 含义 |
|---|---|
| `moe-ub-configured-service/3` | 顶层保留 run_id、mode、execution、model、requests；groups 中每项为 group_id、model_spec、memory、scheduling。 |
| `moe-ub-layer-segments/1` | groups 中每项为 group_id 和有序 layers；每层为完整 `moe-ub-layer-fixture/3`，其中占位 Token 仅用于启动前配置检查。 |
| `requests[].decode_routes` | 每个解码步为按层号字符串索引的路由，例如 `{"1":[0,2],"2":[0,1],"3":[0,1]}`；必须恰好覆盖所选层段。 |
| `moe-ub-service-deployment/4` | 生成文件按组固定成员、映射、本地性、batch_limits 和各层静态配置；后端拒绝运行中改变这些字段。 |
| `moe-ub-multilayer-plan/2` | 一个组的一轮合并图，保留每层 execution-plan/3、逐请求上下文、逐专家输入量及贡献来源。 |

各组使用相同的完整模型结构和所选连续层号，专家放置、容量和调度预算可以不同。逻辑 rank、UB 端点和 die 在组间不重复；同一 chip 的 server 归属一致。各层成员、映射、本地性、批次预算及分块参数在组内不变，所有组使用同一个发送槽数。模板中的图号、轮号、时间和 Token 在每次提交时替换，其余配置在运行内固定。每组声明完整模型，未执行层仍计入容量。

当前显式主机预算为 1～64 组、每组 1～128 个连续层、每张合并图至多 4096 条交付记录，Token/批次预算继承 M7。所选层必须是无 shared expert 的 MoE，role 为 D，next_layer 参考计算为零。每 rank 一张活动图、一个计算槽；层间依赖只连接本 rank 前层末端，没有全组逐层屏障。新请求在本组下次选批时加入，不在层段中途改变成员。旧输入版本继续原行为。

## 样例与手算依据

两组分别使用 rank `[2,7,12]`、`[20,25,30]`，映射到 UB `[0,2,1]`、`[4,6,5]`。各组前两个 rank 同 chip，第三个在另一 chip。复用 M3 的八端点、双交换机网络，共享交换机 8/9 之间的 10 Gbit/s 链路；普通端点链路为 400 Gbit/s。

每组按成员顺序记为 a/b/c，三层专家放置分别为 `[a,b,c,c]`、`[c,a,b,c]`、`[b,c,a,c]`。每个向量为 `256 × 2 = 512` 字节，同目的 rank 去重；fabric chunk 为 256 字节、发送槽为 2。同 rank 贡献不传输，同 chip 的不同 rank 使用本地准备与交付回调，不生成 UB 写；本地交付不按 fabric chunk 切分。

完整模型有一个 dense 层和三个 MoE 层，H=256、V=512、Q=4、KV heads=2、head_dim=64、全部 float16、共享 embedding/输出头。dense 中间维度 512，四个专家各为 256。每 rank 的共有权重为 embedding/最终 norm 的 262656 字节，加 dense 的 1180672 字节及三层各 396288 字节；每个本地专家另加 393216 字节。三层累计本地专家数为 3/3/6，因此权重为 **3811840/3811840/4991488 字节**。

每 Token 的完整 KV 为 `2 × 2 × 64 × 4 × 2 = 2048` 字节，四 Token 一块，共 8192 字节。每 rank 预留工作区 4096 字节、通信缓冲 49152 字节，余量为 5/2/2 个 KV 块。每层保守通信界为 `max(2×3×2×256×4, 2×2×256) = 12288` 字节；不同 rank 可以同时处于不同层，因此层段缓冲界取三层之和 **36864 字节**，不能只取最大一层。声明 20000 字节时，即使足够单层也会被拒绝。

ep0 的 A/B/C（201/202/203）在 rank 2 上分别剩余 3/1/2 个 Token，C 在 1000 ns 到达；D（204）在 rank 7 剩余两个。ep1 的 E/F/G（401/402/404）在 600 ns 到达，分别剩余 2/1/1 个。A/B/C 的最终 KV 预留为 2/2/3 块，B 释放后可接纳 C；rank 2 容量减为四块时，C 等待 A 释放。

参考计算按本地 Token 数计 prepare/finish（200/100 ns），按本 rank 专家输入总量计 expert（100 ns），有专家输入时 local_merge 为 50 ns。本地 dispatch/combine 准备分别为每向量 30/40 ns。上下文和专家分布保存在计划中，目前参考耗时没有上下文性能曲线，也没有总量相同而分布不同的硬件代价模型。

## 已验证命令与结果

在主仓库根目录完成[独立构建](../../docs/environment.md#2026-10-03m8-多层多请求多组联合执行)后，使用现有虚拟环境，输出目录必须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.service \
  --input fixtures/m8_composed_service/service.json \
  --layer fixtures/m8_composed_service/layers.json \
  --backend build/m8-composed-service/output/cosim/ns3.44-cosim-service \
  --system fixtures/m1_compute/system.json \
  --network fixtures/m3_shared_bottleneck/network \
  --observation fixtures/m3_shared_bottleneck/observation.json \
  --output-dir runs/my-composed-service

PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_composed_service.py \
  --build-dir build/m8-composed-service --output-dir runs/my-composed-check
```

最终检查保存在 `runs/v03-composed-service/composed_service-verified`，15 次正常运行及 11 项预期拒绝通过。全部七个请求完成，共生成 12 个 Token，36 条逐请求逐层路由各消费一次，KV 和计算/网络资源排空。

| 对照 | 实际结果 |
|---|---|
| 重叠基准 | 五轮，124 次写、31744 业务字节、38 次同 chip 交付，39951 ns 完成。ep0 为 ABD、ACD、AC，ep1 为 EFG、E。 |
| 独立推进 | ep0 在 18442 ns 开始第二轮，此时 ep1 首轮到 19752 ns 才结束；ep1 第二轮又在 ep0 第二轮结束前启动。 |
| 层间交叠 | 首轮某 rank 的第二层在 6284 ns 开始，远端第一层直到 7874 ns 才结束；逐节点依赖与 ASTRA 原始日志一致。 |
| 单独运行 | ep0 首轮 11570 ns、全部 31921 ns；ep1 首轮 12170 ns、全部 16113 ns。请求/路由工作量与重叠场景逐组相同，共享竞争使两组后续提交推迟。 |
| 旧队列保留 | 334 字节包在 497 ns 入队、763 ns 出队，跨越第二组的 600 ns 启动事件；原生 PortTrace 与队列/发送观测逐项一致。 |
| 瓶颈改为 1 Gbit/s | 相同工作量，完成 339142 ns；两组的第二轮提交均后移。 |
| 容量和预算 | 四块 KV 时 ep0 为 ABD、AD、A、C、C；ep1 Token 预算降到 1 时首轮仅 EG，F 已驻留并继续占 KV。ep0 序列预算降到 1 时为 AD、AD、A、B、C、C。 |
| 边界和本地性 | 空输入在 0 ns 结束，迟到 C 在 500000 ns 才启动；只选第二层正常完成。移除 chip 声明后，原同 chip 交付变为 fabric，200 次写、51200 字节、零本地交付。 |
| 场景与被动观测 | YAML 入口的实际三层结果与基准一致；来源开/关/不挂观测的执行一致，开/关 observed 文件一致，整包与业务字节分别核对。 |

前端拒绝缺失层路由、组间端点重叠、放置不符、层段缓冲不足、不可能的最终 KV，以及缺少组内预建通道。后端独立拒绝层间边界、上下文、跨层重复 tag、静态放置与层序篡改，均在 0 ns、业务提交之前失败。

这一步验证的是选定 MoE 层段的连续解码；dense 层 0、embedding/最终 norm/输出头/sampling 未执行，完整模型 ITL/TTFT、真实路由、目标 profile、MLA、多层 P/D 和完整 CM384/CANN 行为均未在本步验证。M9 等待用户确认。
