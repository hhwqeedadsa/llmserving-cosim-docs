# M10 目标参考合同

[reference.json](reference.json) 固定本次离线核对的来源和参考选择，**不是服务、模型或场景运行入口**。runtime_configuration 和 formal_run_ready 均为 false。

完整推导、实际代码缺项、验证结果和下一步范围见 [目标就绪核对](../../docs/target_readiness.md)。核对入口是 scripts/audit_target_contract.py，需要本机已有的 M9 结构/轨迹文件和已固定的 DeepSeek-V3 小型源码文件，不自动下载或执行外部代码。

INT8 权重/scale、BF16 压缩 MLA 缓存、64 GB 条件预算和 4 GiB 工作区均是明确参考选择，不能用作线上配置或目标 NPU 校准声明。路由 manifest 只覆盖两个独立请求，不代表完整目标工作负载已取得。
