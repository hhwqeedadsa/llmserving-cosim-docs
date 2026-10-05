# 单层执行参数对照

本目录验证 M6 首项的单层、固定轮次执行参数化。输入仍使用 `moe-ub-layer-fixture/1`；专家数由 `expert_placement` 长度确定，专家编号为该数组索引。`rank_ids` 为非空、升序且唯一的逻辑 rank，`rank_to_ub_node` 按相同位置给出一一对应的 ns-3-UB 计算端点，不要求同号。

| 输入 | EP / 专家数 / Top-K | 逻辑 rank → UB 节点 | 独立预期 |
|---|---|---|---|
| `ep3.json` | 3 / 5 / 3 | 2→3、7→0、12→2 | 专家输入数 2、3、1；15 次写、18432 字节 |
| `ep2.json` | 2 / 3 / 1 | 4→1、10→3 | 专家输入数 0、2；3 次写、1536 字节 |

两例复用 `fixtures/m2_single_layer/network` 的四个端点和交换机，不改变其拓扑、链路或协议。EP3 中 A 在 rank 2 选择专家 0、1、2，B 在 rank 7 选择专家 0、3、4；专家放置为 `[2,7,7,7,12]`。三个远端有向贡献为 2→7、7→2、7→12。每份 Dispatch 为 1024×2=2048 字节，每份 Combine 为 1024×4=4096 字节；1500 字节分块后分别为两块和三块，总计 3×(2+3)=15 次写。A 命中 rank 7 的两个专家仍只发送一份向量；rank 12 没有本地 Token，仍接收并计算远端输入。

EP2 中 A 从 rank 4 访问专家 2，B 在 rank 10 本地访问同一专家。仅有一份远端输入及返回结果，分别为 512×2=1024 和 512×1=512 字节；512 字节分块得到三次写。rank 4 没有专家计算，但保留发送、接收及后续依赖。

已验证命令从主仓库根目录运行，输出目录必须不存在：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.generate \
  --input fixtures/m6_execution_parameters/ep3.json --output-dir runs/m6-ep3-plan
build/m6-execution-parameters/output/cosim/ns3.44-cosim-layer \
  runs/m6-ep3-plan fixtures/m1_compute/system.json fixtures/m2_single_layer/network \
  fixtures/m2_single_layer/observation.json runs/m6-ep3-execution
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_execution_parameters.py \
  --build-dir build/m6-execution-parameters --output-dir runs/m6-parameter-check
```

集成检查另构造全空闲成员、单 rank 纯本地工作以及两轮连续执行；检查实际 UB 交付、数据就绪与事务完成、资源回收、观测与来源守恒，以及非法成员、专家和端点。Python 单元检查覆盖其他成员规模与数值边界。功能样例不构成配置白名单，也不构成硬件校准。

保留的执行边界：每 rank 每轮至多一个本地 Decode Token；旧 Prefill 入口仍最多 16 个 Token；发送槽为 1 或 2，应用元数据为零，协议为 URMA_WRITE/RTP；每份计划最多 4096 个传输。逻辑 rank 须满足 ASTRA 的有符号整数和 `id+1` 表索引范围，稀疏大编号会增加 ASTRA 内部表的内存开销。单层与两轮诊断入口保留 `ready_ns + 1000000 ns` 的仿真截止时间，不能据此宣称任意规模已验证。

执行结果的 `rank_ids`、`rank_to_ub_node` 说明数组顺序；`active_wqes`、`peak_wqes`、`initial_tp_counts` 按逻辑成员顺序排列，WQE/目标写事件中的节点号仍为物理 UB 节点。传输结果及内部对应关系另存 `src_ub_node`、`dst_ub_node`，端侧操作继续使用逻辑 rank。`observed/` 不增加内部传输身份。

后续已新增[服务模型与容量参数入口](../m6_service_model/README.md)，在单层服务中验证了逐 rank 容量与非恒等端点映射；旧 schema 的限制保留。同芯片本地交付和场景 YAML 分别见[本地性用例](../m6_locality/README.md)与[场景参数用例](../m6_scenario_backend/README.md)。
