# 同一原生时钟上的分阶段前缀发布

起点 `bdb7392`。本阶段只补 Hybrid 多生产者共享 QP 所需的实际执行边界，不开展论文收益实验，不改变先前关闭的条件和预算。

## 接口与语义

transactions/5 新增可选 `submission_model={"mode":"staged_prefix/1"}`，仅交互 `NativeTransactionSession` 支持。公开 `commit` 增加 `prepare_operations`、`publish_operations` 两个可选整数数组。普通依赖前端和静态 probe 在创建输出/网络前拒绝此模式；已有默认与 `explicit_prefix/1` 行为和字段保持。

- 实际依赖满足的远端 Submit 只预约 ticket、保护源内存，不自动准备或发布。控制只引用此前已收到 submitted 回调的事务，不引用本次新注册或尚未提交的事务。
- prepare 每事务仅请求一次；已有队列信用允许时产生 `descriptor_ready`/`descriptor_ready_ns`，表示描述符就绪。早期 ticket 尚未就绪时，后续生产者仍不能完成顺序 ready 更新；只有连续描述符前缀覆盖该事务时才产生原有 `prepared` 回调。两者都不证明远端写入完成，也不消费 GPU 串行 lane。
- publish 每事务仅请求一次，必须此前已收到 prepared、且该事务 `aggregate_requests=false`。发布至 `max(已有前缀,ticket+1)`；较早的生产者晚 ring 可以不推进。记录 requested/previous/exclusive 以区分请求与有效发布；不当作物理 MMIO 次数。
- 新发布的每个事务恰好一次进入现有实际 MRT1/native 持久传输；源字节仍在发布时捕获，源保护到实际事务完成，put 依赖 ACK、fetch-add 还依赖真实回复。信用释放重新推进待准备操作。不同连接隔离，未发布尾部不自动补齐。
- 完整命令先验证输入/控制/唤醒/结束条件再修改状态；Python 侧拒绝保留 pending，允许合法重试。native 非法命令保留失败结果前缀。descriptor_ready 不允许作为静态 after 依赖；使用公开会话回调驱动生产者，prepared 依赖沿用原有边界。
- 不发明 GPU 准备延迟或预约顺序；调用方负责由实际已观察到的工作回调发出控制。不绑定 Hybrid 完整控制、Combine、硬件成本或最终 GPU slot。

## 验收和停止规则

新版本化小例和稳定 Python API 为用户入口，测试不得成为实验入口。单元覆盖阶段控制纯校验、拒绝原子性、回调与 wake 预算以及默认兼容；检查源保护、actual payload/target/atomic reply、同一 QP 顺序 ready、另一 QP 独立、信用唤醒、晚 ring 无回退和尾部未发布。

至多一次全新公开 `rdma-build`，不复用或改动历史 build。冻结八次网络条件：①多生产者主例（含跨 QP、晚 ring、put 与 atomic）；②深度1信用；③聚合尾未发布的预期截止；④源保护非法写的预期运行失败；⑤native 非法阶段命令整批拒绝；⑥旧默认；⑦旧 explicit；⑧主例从复制 Python 源码/空 cwd 和搬迁且逐 hash 相同的运行时执行。原始失败保留，某条件非预期失败立即停止其后网络，不自动重跑失败条件或增加网络。校验器错误可保存后仅修离线检查，不改变已运行输入和预期。

独立验收读取公开输入、控制 transcript、结果和实际包，不能只复用执行器输出作为预期；比较⑧与①仅允许经逐 hash 证明的 ns-3 库绝对路径及枚举顺序差异。保留代码/输入/工具链/库 hash 和原始命令，离线复制重算核验。

## 资源预算

新目录 `runs/staged_publication_20261027/`；累计受监控2400秒/2GiB新产物（含构建、拷贝、监控及2MiB管理预留）。构建外层900秒/RSS3GiB/地址6GiB，内部配置60秒、编译600秒/RSS2GiB/地址4GiB/512MiB；只有一次构建，无自动 resume。其他单命令60秒/RSS1GiB/地址2GiB，单次网络沿示例明确5ms仿真/60秒宿主/64MiB产物及16MiB结果上限；所有根保留8GiB空闲。执行前检查相应脚本和剩余额度。超限停止、保存前缀，不抬限、不安装下载、不执行GPU、不改依赖、不push。平台与研究说明分别提交。平台验收通过不能证明创新成立，整体目标继续进行中。
