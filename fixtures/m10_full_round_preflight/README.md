# 完整首轮构图与加载的资源预检记录

本目录记录 2026-10-04 在 `e148169` 基线上进行的首次完整首轮资源测量。它只测 Python 构图/导出和 C++ 计划加载的宿主开销，未启动目标网络。此次在构图阶段超时；后续处理经过[索引优化](../m10_cann_indexing/README.md)、[存储优化](../m10_full_model_storage/README.md)和[资源工程](../m10_resource_efficiency/README.md)，最终首轮验收见[交付记录](../m10_research_slice/README.md)。

## 输入与工具

[input.json](input.json) 固定原始来源、模型、成本模板及摘要；[result.json](result.json) 保存阶段、资源和回归结果。输入为 61 块、128 rank、1024 请求，取每个已核验文件的第一个 Decode 入口，共 59,392 条层路由。8192 B 分块、一个发送槽和全部原校验保持。完整 64 步原始输入未改，首轮停止不表示请求结束。

参考成本由小型全模型样例展开，用于生成完整依赖图和测量宿主成本，没有目标 NPU 校准。当前模型和来源限制见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)。

`moe_ub_cosim.full_round_probe prepare` 逐文件检查后生成首轮 bundle；`target` 调用生产构图与导出路径，记录函数入口/返回的时间和 RSS。`cosim-plan-load-probe` 使用服务的计划与 Chakra 校验器，但不创建网络、安装活动图或推进仿真时间。阶段日志包装在正常及异常退出后恢复原函数。

## 当次结果

| 阶段 | 结果 |
|---|---|
| 来源准备 | 38.144 s，采样峰值 428,302,336 B；派生 bundle 为 67,825,336 B。 |
| 完整构图 | 900.469 s 后触及 `wall_limit`，worker 为 SIGTERM/-15，外层退出 1；无残留后代。 |
| 已返回层 | 27/58 个 MoE 层，layer_id 3～29；1,753,718 次交付、3,526,882 个节点，与独立计数一致。 |
| 停止位置 | 正在构造 layer_id=30；完整合并尚未开始。 |
| 采样峰值 RSS | 5,786,435,584 B，约 5.39 GiB，未触及内存上限。 |
| 目标导出及 C++ 加载 | 未执行，没有完整图可供导出与加载。 |

返回层耗时 30.795～35.586 s，中位数 33.210 s。CPU 采样显示当时主要消耗在计算；未做函数级剖析，不能把全部耗时归到某个循环。源码中的 CANN 状态计数对每个 rank、每个专家扫描贡献列表，是下一步核对的具体候选。

七项单元检查及小型实际 C++ 加载通过；小图为 171 节点、39 个跨芯片写描述符和 12 个本地交付描述符。JSON 节点损坏和 Chakra 截断均被拒绝。61 块小 EP 与穿插 dense/无 shared expert 的网络回归通过。首次检查误写错误消息子串的失败日志保留，修正的是测试匹配。

## 复现入口与预算

先运行不需要原始路由的小检查。需已有兼容构建，输出目录必须不存在：

```bash
PYTHONPATH=python python tests/integration/check_full_round_probe.py \
  --build-dir build/m10-full-round-preflight --output-dir runs/full-round-small-check
```

目标历史入口为 `python -m moe_ub_cosim.full_round_probe prepare|target --help`，需要 input.json 引用的本地固定文件。用 `moe_ub_cosim.host_probe` 包裹：准备阶段限 300 s、RSS 4 GiB、地址空间 6 GiB、产物 1 GiB；目标构图和导出共同限 900 s、RSS 32 GiB、地址空间 40 GiB、产物 24 GiB，计划 JSON 另限 20 GiB。完整导出并核验后才启动目标加载。RSS 为采样值，不能解释为模拟 HBM。

准确 argv、工作目录、退出状态及阶段日志位于 `runs/v03-full-round-preflight/`，完整历史命令见[原预检说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_full_round_preflight/README.md)。复测当前实现会得到新结果，应另存输出和版本，不覆盖本目录历史证据。
