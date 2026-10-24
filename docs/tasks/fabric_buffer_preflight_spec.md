# Scale-Out启动前的交换机缓冲检查

平台基线`658d7c8`。前项研究的输入在原生建网时才因PFC headroom失败；本增量将同一约束提前到所有Python原生RDMA入口，并提供离线报告。单独提交，不重跑研究失败条件、不改网络行为或提高容量。

1. 根据实际物理交换机端口，逐端口复刻固定Fabric的整数公式`((rate_bps // 1000000) * delay_ns // 8000) * 3`，每端口reserve为固定ns-3的4096B。不按平均RTT或全部路径重复计费。
2. 每交换机要求`total_headroom + total_reserve < switch_buffer_bytes`。报告端口贡献、共享剩余额度及`remaining >> 3`初始PFC阈值；非法值包含交换机号、所需/配置字节和主要端口。
3. 输入端口连续且唯一，拒绝非法类型、端点、速率/延迟及固定原生uint64/uint32溢出。不模拟溢出后的错误接受。
4. `scaleout rdma-preflight --topology <dir> --protocol <file> [--transactions <file>] --output <new.json>`只检查输入，不需要backend。容量不满足仍保存报告并exit1；可选事务输入同时检查现有地址/依赖规则。通过只证明所列静态检查，不证明运行完成、队列安全或硬件校准。
5. `validate_probe`共同路径在启动前强制检查，覆盖普通probe、stream、事务、在线NativeTransactionSession和service。有效输入不改写配置/结果；绕过Python的原生程序保持原检查。
6. 验证严格边界、混合速率整数舍入、单个/累计溢出、非连续端口与bool拒绝。反例来自前轮250us输入；用已保存原生端口报告独立比对合法输入。非法公开调用不启动backend、不创建运行目录。同步帮助与指南。

阶段`/dev/shm/moe-fabric-preflight-20261024/`，每命令60秒/1GiB RSS/2GiB地址/16MiB；累计受监控180秒/新增32MiB含持久摘要，tmpfs预留8GiB/根盘64MiB。只用现有Python与保存结果，无构建、网络重跑、下载、GPU或依赖改变。失败保留、不自动扩限。该增量是输入鲁棒性修复，不是论文创新。
