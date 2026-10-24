# 应用窗口引用与本地使用寿命

基线 `8f0869a`。本增量落实窗口生命周期研究已确认的“逻辑子窗口共享底层分配/静态注册”关系，并为后续逐句柄退出提供操作入口。不是故障恢复方案或论文贡献。

## 输入与行为

v4/v5 opt-in `application_window_model: {"mode":"local_views/1"}` 与 `application_windows` 同时出现，并要求已有 NIC-scoped registrations。每个视图恰为 `id owner window_key address bytes`；id全局唯一，1..32个，范围必须包含在owner物理窗口内。视图共享物理存储及已有注册，不创建MR或重复计费。不自动推断上游可复用条件：底层分配和静态注册已由输入给定，真实flags/handles/跨rank一致性协商不在本模式中。

操作可选 `application_uses: [id,...]`（1..8个、独有且属于执行GPU）。启用后每个本地source/copy源/目标/local_wait范围必须由所列某一视图完整覆盖；不使用远端目标视图作为发送准入依据。实际本地准入或远端Submit时登记使用，直到实际操作completed时释放；排队等待/源隔离/仅prepared、applied、request_acked均不能替代操作完成。关闭为纯本地节点的 `release_window: id`，不与copy/wait/uses混用；其本地duration结束时才释放应用引用。

静态准入保守要求：每个close显式或经前驱传递地覆盖该视图此前所有使用的completed事件；不得使用先前已声明close的视图（即使close尚未实际执行）。不能以数组顺序、prepared或applied替代完成。运行时再次拒绝关闭使用中的视图、重复关闭、跨owner使用和关闭后的使用。在线追加纯校验完整候选，拒绝不改变已运行状态；不读取未来内存或内部真值。

最后一个应用引用释放只记录 `no_application_refs_provider_pending`，静态注册仍有效；不会调用或模拟provider退出，也不宣布物理字节可安全释放。远端实际包仍经原有NIC/rkey/range/atomic权限写同一物理存储，不能用关闭metadata神奇取消在途或未来发送的旧授权。

## 验收

- 纯核心对共享引用、使用中关闭、重复关闭、归属、失败原子性、最后引用pending做独立反例；默认无新字段时完整result逐字节保持。
- Python和native在分配网络/输出前拒绝成对字段、类型、范围、owner、漏用视图、关闭前缺completed、关闭后用及重复关闭；v4/v5和在线追加均覆盖。
- 小型真实网络：关闭一个共享视图后另一视图可继续本地copy；最后一个目标视图释放后，源GPU仍使用其本地视图发送，实际包正常写入（不误称安全复用）；源视图使用完成后才能关闭。审计实际payload/ACK/视图事件/最终字节。
- 独立checker拒绝关闭时间、引用数、实际数据等篡改；新源码根/空cwd公开小流程与离线重算通过，证据持久化。新增示例、CLI帮助、语义及输出边界同步。

## 限制

这是声明式、本地应用引用的功能模型。没有实现真实公开注销、MR分段/逐连接handle退出、provider错误/未知、DMA fence、缓存引用、重新注册/发布、CUDA可见性或成本。不得把最后引用pending当成MR弱失效模型，不代替正常mlx5强退出、应用排空、独立底层分配或备用窗口基线。论文主创新、收益、合法故障并发、校准与千卡执行仍未完成。
