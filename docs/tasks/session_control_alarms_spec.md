# 在线控制定时器与GPU本地队列分离

平台增量，基线`2b72dfa`。上一轮发现把100μs业务timer表示成rank4本地计算会阻塞同rank结果复制。本轮新增显式控制定时唤醒，不改变既有GPU FIFO语义，不重跑上一轮16条件研究，不将测量修正当论文创新。

## 接口与不变量

`NativeTransactionSession.commit(nodes, finish=False, alarms=None, cancel_alarms=None)`新增两个可选参数；省略时COMMIT和默认结果形状及内容保持。显式`alarms=[{"id":整数,"delay_ns":整数}]`在当前原生ns-3时刻之后安排一次控制事件，`cancel_alarms=[id]`显式取消仍待触发的控制事件。没有第二套时钟、sleep推进、预设网络完成或GPU rank/本地队列占用。timer本身没有网络包或隐含控制报文成本，不称真实CPU/GPU故障检测。

- ID与operation分属不同命名空间，0—65535；累计至多32个，不复用已取消/触发ID。delay可为0，截止必须严格早于本次max_sim_ns，避免全局Stop同纳秒遮蔽触发。
- 原生真实触发后才回调`{"alarm": id, "event": "fired", "time_ns": 当前原生ns}`；一次且不携带operation、ACK、目标应用或内部故障真值。同纳秒只保证时间与事件集合，不承诺独立回调顺序。
- 只能取消已注册且仍pending的ID；重复、未知、已触发/已取消、类型错误、超额及截止外均拒绝。整批操作与控制命令纯校验通过前不得注册前部工作或取消/安排计时器。
- `DECIDE.drained`仍只表示既有工作及物理网络空闲，QP保留；并不证明控制timer为空。仅pending timer时可提交空COMMIT等待；最后timer取消且其余全部空闲时必须显式finish或递交能继续的工作/timer，不能形成无唤醒的空等待。
- 显式finish要求工作/网络排空且本批取消后无pending timer；禁止隐式取消和边建timer边finish。允许有过控制timer、但无数据操作的会话正常结束。异常、未完成和源失败不因timer伪装成功。
- 使用该功能时`session.control_alarms`记录armed/deadline/fired/cancelled时间；不使用时不增加结果字段。所有源端失败资源、在途副作用、QP及内存保护规则保持。

## 验收与预算

验收包括：不用timer的新旧完整result字节相同；真实接收后复制不被未来timer阻塞；GPU忙时控制timer准时触发且不伪造GPU完成；空闲timer后继续同一QP；零时延/同纳秒多timer、operation同ID、取消、无数据timer会话；前端与绕过前端的原生非法命令及整批无副作用拒绝；源失败后timer仍驱动健康工作，partial退出/旧租约与真实包保持；旧在线/事务与前缀相关回归。独立核对控制记录、实际包、目标字节和复制时刻，并拒绝篡改触发、内存及重复回调。

目录`/dev/shm/moe-control-alarms-20261024/`。单命令60秒/RSS1GiB/地址2GiB/产物64MiB；全阶段累计受监控450秒/新产物192MiB，含失败、构建、检查点、恢复与持久副本，tmpfs预留8GiB。只编译受影响session入口，复用已固定的其他对象和8原生库；不安装下载、GPU执行、修改依赖或重跑旧Prefill/千卡失败。失败保留，不自动扩限或无修改重跑。验收后独立本地提交、不push；论文与整体目标仍in_progress。
