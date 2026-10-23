# 实际接收缓冲作为后续put的数据源

从干净`bcc73d6`继续。本轮是平台增量，不做性能研究：MRT1事务新增`moe-rdma-transactions/3`，保留v1/v2成功结果及输入。v3沿用v2本地节点，根增加固定`source_model`：`capture=registered_window_at_publish/1`、`release=transaction_completed/1`。put二选一提供hex或source（key/address/bytes）；source始终属于connection.src，长度1—32768B，须完整位于已注册窗口。禁止其他owner、hex+source、atomic source及布尔/负/越界字段。v3即使不使用source也显式声明模型。

源范围在事务提交时取得读使用记录（lease），允许重叠读，不占额外网络额度；排队期间保持。真正发布给载体前读取当时窗口字节并编码进MRT1；不得从初始配置或上游操作表拼接后续payload。该功能模型在publish处快照，未模拟逐包DMA。源使用记录到MRT1应用事务实际completed（本put为请求ACK并按序回收）才释放；不是GPU source-safe或真实CQ成本。所有网络/本地阶段仍共用一个原生Simulator及持久QP。

本地声明处理尚无数值输入/输出或输入lease，不能将此源put保护解释为完整应用buffer寿命。额外保留一个提交前合法覆写的对照：在本地阶段条件消费之后、后继put提交之前覆写接收区，后继必须读取新值；它明确暴露未建模的计算输入保护，不能偷偷提前锁住所有未来source来掩盖该缺项。

活跃源范围禁止本地清零、普通远端put或原子写入；初始化、读及相邻范围不受影响。检查按owner及物理区间，不能用不同key或epoch绕开；窗口原有不重叠限制保持。重复已应用请求直接返回缓存，不能因后续读使用记录而重写或重复原子。多个本地消费先检查全部reset可写，再做任何修改，避免部分清零。发现冲突不等待、不修改源，不虚构硬件反压；v3运行期错误停止并保存真实部分结果与execution_error，v1/v2失败规则保持。

v3事务报告增加source_model/source_events/active_source_leases；acquire/capture/release与实际接收、条件消费共用order。记录只用于离线核验，不写入包、不改路由或优先级。capture保存源范围及实际快照hex；实际源包和目标最终内容由独立解码/内存/lease重放核对，不能仅比较两份报告字段。无source的v3去掉新增字段后与v2相同。

公开人工例从GPU0发送分包字节到GPU4；实际收到并处理后，从GPU4接收窗口向GPU1转发，再从GPU1回传GPU0。改变最初payload应在无下游hex变更的情况下改变全部后续包；错误条件不能提前发；同源多读和深度1排队均保持保护，实际完成后合法覆写可继续；5ms截止保留活跃lease而不伪造释放。远端put/原子及本地reset冲突有独立反例，确认源保持且没有部分消费。旧v1/v2、事务/窗口/队列/本地阶段回归、纯字面量v3、重复/id改名均核验。

每命令60秒/RSS1GiB/地址2GiB/阶段64MiB，仿真5ms/结果16MiB，累计受监控480秒/192MiB，归档成员128MiB/压缩48MiB，内存盘预留8GiB；原始件`/dev/shm/moe-source-windows-20261023/`仅易失，无持久备份。只编译两个受影响对象和独立内存检查；不安装下载、改变固定依赖、执行GPU或重跑旧失败Prefill/1024。保留失败，不抬限重试。功能边界、可复用例、独立流程、恢复记录及本地提交交付，不push。现代provider、自发物理路径、GPU可见/实际数值专家、校准及论文主贡献仍未成立。
