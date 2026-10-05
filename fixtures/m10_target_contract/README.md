# 目标部署参考选择的早期核对

[reference.json](reference.json) 固定早期离线核对采用的来源与参考选择，不是服务、模型或场景运行输入。其 `runtime_configuration=false`、`formal_run_ready=false` 保留该次核对的含义，不能用于判断当前实现进度。

核对入口为 `scripts/audit_target_contract.py`，需要清单引用的本地 R1 结构、两份路由文件和固定 DeepSeek-V3 源文件；不自动下载或执行外部代码。INT8 权重/scale、BF16 压缩 MLA KV、64 GB 设备预算及 4 GiB workspace 均为参考选择。

当前参数与适用范围见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)，后续 1024 请求清单见[来源选择](../m10_route_selection/README.md)，完整首轮结果见[最终验收](../m10_research_slice/README.md)。原始推导与当时缺项保留在[历史核对原文](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/docs/target_readiness.md)。
