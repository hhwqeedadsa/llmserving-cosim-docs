# Self-peer GDAKI 建连源码执行核查

目标：明确现代 DeepEP 的自发 GIN 请求对应哪些持久 QP（队列对）及建连规则，为原生网络接入确定必要资源；不推断自发请求的物理路径或时延。

范围：固定 NCCL `12df1a11afad322be5a204a2db890161cbf8131d`，FULL/rankStride=1；按原文提取信息填充、MTU选择、QP建连函数和两段self-peer建连循环。在普通CPU程序中执行这些未改写片段，用记录型替身替代外部API。不是运行NCCL、DOCA、GPU或NIC。替身类型、常量和错误码只服务控制流检查，不验证供应商ABI。

竞争解释：self-peer主QP连回自身；主QP连接独立responder；self-peer继承远端LAG选口。检查实际目的QPN、双向连接、地址、INIT/RTR/RTS修改及LAG设置。另检查所有合法MTU组合、非法MTU、ordering不匹配和中途失败清理。独立Python核验按对象配对枚举，不调用生成器计算期望。

资源核算限FULL且nCounters=0；分开请求context数、按connection向上取整的分配数、跨rank主QP、self主QP和responder。不把对象数解释为显存、活跃工作、线上包数或千卡执行能力。已找到固定初始化头的ginCounterCount=0，以及DeepEP该建连路径不覆盖此字段的源码依据；counter1仅为分支控制，不作为本配置。

预算：原始件`/dev/shm/moe-self-peer-provider-20261023/`，每子命令60秒、RSS1GiB/地址2GiB、输出32MiB，受监控累计300秒/128MiB；归档成员32MiB/压缩16MiB，内存盘保留8GiB。仅离线复用已有固定源码，不下载、安装、改依赖或执行仿真/GPU，不重跑历史失败。保留失败，不自动抬限重试。工作树从`7fac8d7`开始，完成后仅本地提交。

验收：独立核验原始源码commit/blob/SHA、提取片段hash、记录型替身的建连结果与明确边界；新cwd重复与可恢复检查；中文HTML说明后端必须改什么及仍需测什么。研究主贡献及完整现代接入仍未完成。
