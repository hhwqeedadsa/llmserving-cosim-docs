# 真实调用与连接复用：执行前方法

从干净`14f844c`继续。上一轮是实质进展：同一参考负载跨窗口排序反转，但尚无现代库性能与实际调用间隔证据。本轮先判断其应用语义和连接映射适用范围，不添加可调空闲时长搜收益。

核对固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`和NCCL `12df1a11afad322be5a204a2db890161cbf8131d`。已有源码按原tree/blob/SHA256验证；最多只读取得6份缺失小源码：deep_ep/buffers/ep.py、csrc/kernels/ep/dispatch.hpp、csrc/kernels/ep/combine.hpp、csrc/comm/stream.hpp、csrc/utils/event.hpp、deep_ep/utils/event.py。每响应2MiB、单子进程20秒、各URL只请求一次，总6响应/12MiB/120秒；用缓存Git树核对blob和字节数。只解析、不执行外部代码；失败保留，不换版本或重试。后续分析以已取得内容为限。

重点核对：EPBuffer/Context创建与复用、num_allocated_qps和num_qps默认/覆盖、SM和channel到GIN索引/连接映射、数据就绪事件与通信/计算流等待、dispatch→专家计算→combine→后续工作顺序、flush与屏障是否真的排空CNP或重置拥塞状态。区分示例用法与API强制规则，不从源码推断实际ns间隔或硬件性能。已有A100记录不能作为此Hopper+分支校准。

竞争解释：

1. 真实库每调用重建QP/清空拥塞状态：若Context长期保存且屏障只flush传输/等待信号，则否定此解释，但不能仅据源码证明硬件CC具体行为。
2. 第二窗口可以直接视为下一真实Dispatch：若必须经历数据就绪、屏障、计算或Combine，则收缩这种映射；源码部分顺序不等于实际时长。
3. 每peer一QP是现代默认：独立追踪Python默认和launcher选择，核对实际上下文/连接数量；多个上下文与多个物理QP/NIC分别计算，不能按名称当一一对应。
4. 多上下文只改变拥塞粒度：若同时改变ECMP线端口、SM/channel任务划分或NIC/连接，则明确不是单因素反事实；先列出下一轮可部署强基线需要保持的条件。

使用四份已见路由只做独立按源码的token→channel/上下文工作分布核算，不抬现代plan每rank16-token限制或拆76-token窗口伪装真实执行。分析不读取truth产生策略、不读测试fixture。需要时枚举有限小参数核对代数，但不把计数作为时延优化。未预注册新网络，本轮不执行网络/GPU、构建、安装或改固定依赖。每本地命令60秒/RSS1GiB/地址2GiB、阶段32MiB，总受监控240秒/64MiB，归档压缩16MiB/成员32MiB；内存盘预留8GiB，目录`/dev/shm/moe-invocation-semantics-20261023/`。来源缓存小件留data/research/invocation_semantics_20261023（总≤1MiB），大件仅易失内存盘，无持久备份。中文报告、可复用审计和本地提交，不push；论文目标保持in_progress。

取得六份源码后的执行细化（未运行计数/网络）：已确认direct自动申请17上下文，调用推荐min(SM,9)，fresh Dispatch预留索引0给notify。固定六组离线条件：显式num_sms=4/8/16 × 假设可用shared memory=64/128KiB；其余同EP4/H7168/Top8/BF16/fresh direct。由固定launcher计算notify与数据warp数，不手工指定warp；shared memory是假设输入，不贴GPU型号或称设备默认。NCCL连接数按1/2/4分别投影context%connections与context//connections，记录实际上下文数量向连接数取整，不能当新NIC带宽。独立checker从各SM/warp的token循环枚举，与producer的token反解比较；总180写/2603520B保持不变。另只按公式核对无NVLink/direct逻辑域的自动SM分支，不执行源码或自动带宽探测，不把推导值当实际运行读回。
