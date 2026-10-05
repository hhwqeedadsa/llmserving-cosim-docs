# 等待与延迟反馈输入

本目录检验 v0.2 第 12.3 节。四 rank 图直接使用 [M2.1 输入](../m2_single_layer/layer.json) 生成；准备耗时 5000 ns、rank 2 专家计算 12000 ns、局部结果处理 1000 ns 均不变。替身和ns-3-UB 运行使用逐字节相同的四份 Chakra 图。

`given-events.json` 只供 `cosim-layer-delay-test` 使用，给出十四条传输各自的数据就绪、事务完成绝对时刻，单位 ns。transfer_id 为 M2.1 固定生成器的 4294967396～4294967409；依次对应 Dispatch 的 0→1、0→2、1→0、1→2、2→3、3→0、3→1，以及 Combine 的 0→1、0→3、1→0、1→3、2→0、2→1、3→2。这些事件不写入 ExecutionPlan，也不用于ns-3-UB 后端。

`tests/integration/check_delay.py` 从基准表生成以下对照，每次只改变一个事件，其余时刻固定：

| 用例 | 相对基准的变化 | rank 2 专家开始/结束 | rank 2 首次 Combine SEND |
|---|---|---|---:|
| given-base | 无：本地输入 5000、SEND 2→3 完成 18000、RECV 0→2 为 20000、RECV 1→2 为 26000 | 26000 / 38000 | 39000 |
| given-last-input | RECV 1→2 数据就绪改为 30000 | 30000 / 42000 | 43000 |
| given-local-send | SEND 2→3 事务完成改为 34000 | 34000 / 46000 | 47000 |
| given-noncritical-input | RECV 0→2 数据就绪改为 24000 | 26000 / 38000 | 39000 |
| given-unrelated-input | RECV 3→1 数据就绪从 50000 改为 54000 | 26000 / 38000 | 39000 |

首个 Combine SEND 比专家计算结束晚 1000 ns，原因是图中已有局部结果处理。本表不把该处理重复计入网络时间。3→1 的源事务始终在 56000 ns 完成，因此全组 Dispatch 最晚完成为 56000 ns；rank 2 的专家计算不等待该时刻。最后一行确实将 rank 1 专家开始从 50000 移到 54000 ns，rank 2 保持不变。

其他 Combine 给定事件只为完成整组图。所有替身用例中，rank 2 的 Combine 接收 3→2 均在 70000 ns 就绪，最终解包合并在 71000 ns 完成，后继 COMP 在 72000 ns 完成；不能把专家计算推迟 4000 ns 解释为全图必定推迟 4000 ns。

替身调用原 ASTRA 执行层和已验证的 ns-3 时钟/事件调度接口，使用原 WriteCompletion 分别处理数据与事务事件。SEND 登记后才安排给定事件，RECV 只登记等待。替身不创建 UB 网络对象；result.json 明确标记 event_source=test_double。错误的过期事件不会被钳到当前时间。输入身份、类型、范围和覆盖关系先校验；运行时事件早于实际 SEND 也明确失败，保留 ASTRA 日志和最终错误原因。

网络仿真运行直接调用 `cosim-layer`，复用 M2.1 的网络输入，另生成一份仅将 rank 1 与交换机 4 之间链路传播延迟从 20 ns 改为 2020 ns 的对照。其余配置文件、带宽、图、路由、优先级与协议一致；变化对经过该物理链路的所有业务生效。数据就绪必须对应实际 RemoteWriteCommitted，发送完成必须对应原 WQE 事务完成。检查专家开始等于必要本地条件的最晚完成，不要求 ns-3-UB 仿真得到替身时刻，也不要求计算变化等于单条链路增加的 2000 ns。

运行命令及实际证据见 [联合程序说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/backend/README.md#m22-等待与延迟反馈) 和 [历史环境记录](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/environment.md#2026-09-28m2-2-delay-feedback)。这仍是固定参考模板的功能验证，没有真实模型或硬件校准结论。
