# 长时间波形与模型—网络证据复核

日期：2026-10-08。今天做的是**既有实验复核、分析修正与配置验证**，没有新增 GPU 实测或 ns-3 仿真。

## 1. 对照目标的交付

| 用户关心的问题 | 当前证据及回答 | 不能外推的部分 |
|---|---|---|
| 长时间、多波峰波谷、微秒横轴 | 0–1100 µs；10 个 wave、20 个 task、1302 个 data packets；9 段突发间零数据带宽；另有 0.25 µs 放大图 | 释放时间人为设置；不是生产到达序列或完整模型计算自然产生的间隔 |
| 模型参数与模型大小 | Qwen3-30B-A3B，48 层、hidden 2048、128 experts、Top-8；按 JSON 重新求和 30,532,122,624 参数，BF16 56.871 GiB | 不是实测显存；3.353B active 为共享参数表＋Top-8 专家口径，不是每 token 实际访问量或 FLOPs |
| 输入大小及影响 | 128/1024/4096 输入 × 4/32 输出共 6 case；原始请求输出与 110 份 batch 文本 trace 核对一致 | 单请求、TP2/EP2、给定 Profile；不能代表所有业务负载 |
| GPU 类型 | 上游 RTX PRO 6000 Blackwell Server Edition Profile；meta.yaml 的单 GPU profiler、dummy 权重、TP1/2 shape 网格 | 不是本轮 A100 测量，也不是真实多卡 TP2 benchmark |
| 如何连接、各层带宽与可调性 | analytical 两 rank 16 GB/s、20 µs；ns-3 八 host、两 switch，access/core 为 400/100 Gbps，热点组为 100/400 Gbps | 逻辑维度不自动对应物理层级；16 GB/s 是 128 Gbps，不是 16 Gbps |
| 华为 384 是否可用 | 澄清 TP/EP 共享设备；实际校验通过 DP64×TP2×PP3、stage 内 EP128 的 384 设备候选 | 仅配置校验；未执行 384-rank workload，未填华为链路和 NPU Profile |
| 网络竞争 | 11 个已登记 case、63 个完成 task；同向/反向、竞争字节比、固定总量下热点三类控制实验均符合预先预测 | 无真实多 rail、动态路由、拥塞控制校准；不能外推生产 p99 |
| trace 怎么生成、与本工作的联系 | request/batch → operator/collective → Chakra → ASTRA；选定 collective 按 ring 显式展开 src/dst/bytes/dependency → ns-3 | 不是把 LLM trace 直接改扩展名就得到网络流；peer 及释放计划需声明来源 |
| 最细粒度 | 代表 peer：278,528 B → 5 WQE segments → 68 个 4 KiB data packets → hop/queue/port；满包 wire 4174 B 在 100 Gbps 序列化 333.92 ns | 333.92 ns 是一包发送时长，不是仿真时钟精度；0.25/5 µs 是图的统计分箱 |
| LLMServingSim CCL | 实际代码和已生成 trace 都为 TP AllReduce、MoE AllGather＋ReduceScatter；Chakra COMM_COLL_NODE；ASTRA ring/localBWAware | 不执行真实 NCCL/HCCL；旧 docstring 的 AllToAll 不能替代实际输出 |

## 2. 波峰、波谷怎么读

![长时间多峰网络图](../../source/_static/round10/long-window-network-traffic-waves.svg)

来源为 ns-3 AllPacketTrace、PortTrace、QueueTrace 和 task_statistics；证据为 `ns3-simulated/trace-derived`。上图测每个方向、每类流的 data wire bandwidth（Gbps，5 µs 分箱）；中图测 switch 8/9 的 port 4 上 VOQ＋egress queue（KiB，1 µs 采样）；下图为 first-packet 至 last-ACK 窗口（µs），不是 GPU kernel。

黑线是居中 25 µs 移动平均，只辅助读图，不能用其边沿判断真实发送起止。图上反向带宽画为负值仅用于区分方向。带宽不含 ACK/control 小包，但队列状态可能含控制包，两者不能直接作字节等式。

新复核确认所有分箱合计 5,434,548 wire bytes，与 packet CSV 守恒；单方向不超过 100 Gbps。修复了窗口外多一个 bin，以及 W2 标注至 275 µs、数据却只到 260 µs 的问题；新放大 CSV 覆盖 100–280 µs。

QueueTrace 峰值必须区分三种口径：全部回调记录峰值、同时间戳最后状态峰值、固定时间采样峰值。全部回调记录峰值为 670.325/212.099 KiB，包含同刻中间更新；旧的 670.265/208.369 KiB 是同刻最后状态峰值。图中局部 481.0/208.1 KiB 则是 0.25 µs 采样峰值。都不是硬件 buffer counter。

## 3. 模型和网络是怎样串起来的

本工作用 trace **保留通信的模型语义，再用网络仿真解释其耗时与竞争**，不是从随机网络流量反推模型。

128-token、BF16、EP2 的 dispatch 字节由当前代码计算：`(128 // 2) × (2048 + 128) × 2 = 278,528 B`，包含 hidden 和 router logits；combine 的总 buffer 为 `128 × 2048 × 2 = 524,288 B`。两 rank ring 将 ReduceScatter 展开为每方向 262,144 B；不能把 collective 逻辑字节与每 peer payload、wire bytes 混为一谈。

可追踪键为 request_id → batch_id → block_id → collective_id/phase_id → flow_id/task_id → packet PSN → node_id/port_id。Block24 是零起编号 24，即顺序第 25 个 block；原解析器 `(op_index-1)//12` 和 `layernorm_289` 确认这一点。

算子 trace 存 Profile 时长和依赖，不是硬件绝对起止时间。Chakra 节点由 ASTRA 执行；本次选定切片的 peer 排程是显式 ring 投影，长窗口又额外采用人工 release schedule。只有分别标注这几层来源，才能判断某个网络波峰究竟对应哪个模型操作，以及哪些时序仍需真机校准。

## 4. 参数具体改哪里

| 参数层 | 配置入口 | 单位及限制 |
|---|---|---|
| 模型 | `configs/model/...json` | hidden、heads、显式 head_dim、experts、Top-k 等；更改 shape 后需匹配 Profile |
| 请求 | workload JSONL | `input_toks`、`output_toks`、`arrival_time_ns` |
| 计算设备 | cluster `hardware` + `profiler/perf/<hardware>/<model>/<variant>` | 算子库 `time_us` 转 trace ns；不能只改设备名就代表 A100/NPU |
| 逻辑网络 | cluster `link_bw`、`link_latency` | GB/s、ns；标量复制到各维，列表长度必须等于逻辑维数 |
| 物理网络 | ns-3 `topology.csv` 每条 link | bandwidth（例如 Gbps）和 delay（例如 ns）；另配 routing/placement |
| 网络协议/观测 | ns-3 `network_attribute.txt` | 应从当前 runtime catalog 生成完整快照；不把旧样例默认值当成通用事实 |

`network.yml` 每次由 config_builder 重建，稳定调参应修改 cluster 输入。审计调用真实 `_normalize_network_dim_values` 验证了标量、多维列表及长度错误拒绝；API 测试列表 `[50,12.5,25] GB/s` 仅为占位值，绝非华为规格。调整网络带宽不会自动改变 GPU 算子 Profile 的时间。

## 5. 384 设备配置修正

![384逻辑设备映射](../../source/_static/round09/huawei-384-logical-mapping.svg)

来源为当前 config_builder 实际执行及 `audit.json`；物理量是设备、rank、block 数（count），证据为 `config-validator-only/design-hypothesis`。不是互连物理图，也不是华为性能图。

64 个 instance，统一 `tp_size=2, pp_size=3, ep_size=128, dp_group=同一组`，每 instance 6 台设备，总计 384。ASTRA dimensions 为 `[2,3,64]`，EP mask 为 `[true,false,true]`，因此 EP 在各 PP stage 内，不跨 PP stage。

原 DP3×EP128 和 DP3×TP2×EP64 将 EP 误读为独立设备维度；按三实例 DP group 直接配置，分别因 128/64 不能被 3 整除而失败。新候选改变为 PP3，并不与旧的“三独立副本”语义等价。要讨论华为 384，下一阶段仍须设备型号/数量定义、NPU 算子 Profile、物理 placement、节点内/超节点内/外链路及通信运行时资料；当前完成的是可行性与接口边界调研，不是硬件复现。

## 6. 实验复核与剩余边界

六组请求的 TTFT/TPOT、原始输出、110 份文本 trace 的 collective 数及字节、保存的 `.et` 和 network/system 配置均已核对；11 个网络 case 的输入、全部 63 个 task 完成记录、FCT/phase 指标与预测分类均已复核。长窗口全部 20 task 和 packet payload 守恒，6 项时间窗口分析回归通过。上游 simulator core 未修改，因此没有把历史 58/58 回归称为今日新跑的测试。

同向竞争把目标 FCT 从 23.231 增到 45.683 µs，反向为 23.731 µs；固定总字节的目的端热点把 phase 延长到均衡的 3.707 倍。这些结论只属于登记的受控拓扑。长窗口 W1 为 45.937 µs，与孤立 case 的状态不同，不应作为完全同一实验重复值。

真实多机校准、真实 NCCL/HCCL channel/chunk 捕获、Huawei 384 workload 执行、生产到达 trace 驱动的完整模型长时间波形都**未完成**，也不被本次配置校验或守恒测试证明。当前交付完成的是目标中的可视化、必要仿真实验、参数/trace/CCL 调研与边界说明。

## 7. 复核入口

```bash
python3 research/goal-audit-20261008/sync_rebuilt_artifacts.py --workspace /path/to/a100_ep_runtime
PYTHONDONTWRITEBYTECODE=1 python3 research/goal-audit-20261008/verify_evidence.py --workspace /path/to/a100_ep_runtime
```

`audit.json` 保存实际计算结果，`sources.csv` 保存输入、原始输出、代码与图的 SHA-256。源工作区的第10轮 `analysis/test_long_window.py` 提供 6 项回归；其中 packet/port 身份匹配还由 `analyze_long_window.py` 在重建时检查。网站以 `sphinx-build -W --keep-going` 构建与 linkcheck 验证。脚本不运行模拟器、不改模拟器源码，也不证明未登记硬件的准确度。
