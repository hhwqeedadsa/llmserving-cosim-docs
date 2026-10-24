# 持久字节与事务恢复验收

从256dfc2实施可选`transport_recovery`。用户入口、参数和失败语义见[持久恢复示例](../../examples/scaleout_rdma_streams/README.md#可选的持久连接丢包恢复)，不依赖本fixture运行普通实验。源码规则见[规范](../../docs/tasks/persistent_recovery_spec.md)。这是平台功能证据，不是论文创新、硬件校准或生产可靠性结果。

`build.py`使用本机已固定的编译/链接收据隔离构建三个入口，旧runtime与8原生库逐hash保留。正常部署通过公开`rdma-build`，不依赖这些历史收据。`run.py`经公开CLI和必要原生绕过检查验收15条件；`--reuse-completed`只复用同输入、同命令、原结果hash核对过的已完成条件，用于修正检查器后的继续，不擦除失败或重跑网络。`edges.py`补独立CNP、在线返回驱动后继、v4/v5、真实截止和6类CLI/native参数拒绝；输入不变才允许复用。

主条件验证中间/尾数据及最终ACK各丢一次、跨多个已发布work、空闲后同QP追加、Karn跨工作采样、过短RTO的实际误重传和迟到累计ACK，以及有限重试耗尽。事务原子响应数据/ACK重传，按真实包恢复，最终窗口与必要副作用保持。在线控制器只依据收到的原子返回7生成后继107；没有预载后继字节。单流没有CNP的结果保留，双流明确排队后才用于CNP/丢包组合验收。

`audit.py`仅标准库，按实际源包、捕获的线端头、已发布work、源ACK和恢复记录检查序号与截止；从载荷独立解码MRT1并按实际目标顺序核对原子旧值、返回值及最终窗口。共149个源数据包、124个所选端点TX见证和304条恢复记录；接收捕获仅配置GPU0/4，未覆盖的GPU1源包不冒充逐包捕获验证。拒绝提前超时、伪造ACK、篡改原子副作用三类错误。CNP组合和v4/v5输入另外核对，不以此声称完整现代MoE。

纯C++检查20项持久前缀/源端不变量；Python恢复输入、原stream与事务输入检查通过。原持久工作流7条、远端事务8条、在线会话3条公开命令及其直接原生/交互检查通过。旧默认完整result逐字节相同；显式恢复的被动stream身份关联可以改变捕获ID，网络业务、源字节和队列保持核对。

首次检查器误把事务transport当顶层字段，成功网络按hash复用；一个ECN=0配置在CLI前置拒绝，随后使用合法正阈值；单流无CNP的控制结果保留，双流组合另行执行。归档离线结果首次因目录枚举顺序导致JSON键次序不同，解析内容相等，已排序输出后重做离线归档。第一次归档未执行恢复网络，未重跑旧研究失败。修正本fixture的输入复用保护发生在归档完成之后，该辅助脚本与包内版本有差异，另补充lease_failure.py及独立收据，详见sources.json；平台与实际回放代码保持一致。

额外源租约失败检查见[lease-failure.json](lease-failure.json)及[独立包核验](lease-failure-audit.json)：目标在6,156ns只写一次，三次请求ACK真实丢失，两次超时重发后在70,000ns耗尽；请求ACK/事务完成仍为-1，257B源租约继续保护。其输入/完整部分结果单独持久保存，未纳入更早的主检查点。

[verification.json](verification.json)、[independent-audit.json](independent-audit.json)、[recovery.json](recovery.json)、[closeout.json](closeout.json)及[sources.json](sources.json)保存实际证据。640成员/59,647,125B检查点逐hash核对，压缩12,250,863B；新源码根/空cwd离线核验逐字节相同，并执行4公开操作、3个实际小网络，完整结果除库路径外相等，8库均从包内加载。仍依赖宿主Python、系统loader和C++系统库，不是完整离线依赖构建。

原件`/dev/shm/moe-persistent-recovery-20261024/`为易失内存盘；本地持久检查点及hash见closeout。每命令60秒/RSS1GiB/地址2GiB/64MiB、仿真5ms/结果16MiB；累计受监控600秒/新产物256MiB、归档成员128MiB/压缩40MiB及8GiB预留。没有依赖更新、安装下载或GPU运行，不push。连接故障重建、跨代际内存安全、真实现代事件和硬件校准仍未完成，论文整体目标继续进行中。
