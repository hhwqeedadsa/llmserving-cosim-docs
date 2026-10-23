# 本地处理的实际输入输出及复用边界

从干净`d054a0f`继续，平台功能增量。前轮反例已证明本地处理阶段不保护输入，后继put可读到期间被覆盖的新值。本轮新增`moe-rdma-transactions/4`，保留v1/v2/v3输入、输出和行为；不把字节复制模型当作真实专家数值计算或时延校准。

v4沿用v3字段，根增加固定`local_memory_model`：mode=`guarded_copy/1`、acquire=`admission/1`、capture=`start/1`、commit=`completion/1`。local节点可选`copy`，含source={key,address,bytes}、target={key,address}，均属于节点rank；长度1—32768B，完整位于注册窗口。源目标不得重叠；本节点reset不得与二者重叠。布尔、负值、未知字段、foreign owner、越界均拒绝，包括永远无法就绪的节点。无copy本地节点保留旧语义；不能借v4宣称所有本地计算均有输入保护。

条件及前驱满足后，先检查全部reset、源可读、目标可独占写，再执行消费、取得源读使用记录和目标独占写记录后入队。在每GPU串行槽排队期间也持有记录。本地源可与其他读重叠；独占目标不得与其他读/写使用记录重叠。普通远端put/原子、本地reset和后续源读取均遵守保护，禁止偷偷绕过key或owner。重复已应用远端请求仍返回缓存，不再次写入。冲突作为功能错误保留部分状态，不虚构硬件等待、带宽或反压。

开始事件实际读取当前注册源字节，完成事件将该快照一次写入所声明目标；成功提交输出后释放两个记录，再标记本地完成并推进后继。零时长也经过调度事件。输出发布前的读取及处理期间输入覆写必须拒绝。输入和输出区在完成后可合法复用；跨阶段保护需应用明确依赖，没有自动保护所有未来source。LE64条件读取也不能读取被独占写保护的区间。

v4事务报告新增local_memory_model/local_memory_events/active_local_memory_leases；acquired/captured/committed/released与接收、条件和source事件共用order和ns时钟。capture和commit含实际hex；报告仅供离线核验，不编码进线上包。旧source_events/active_source_leases只包含远端put的源记录。本地完成前遇冲突不标记完成；截止保留已入队未完成、活跃读写和旧输出，不补写或释放。单次事件检查失败不应部分reset或泄露本地记录。

失败报告读取pending当前条件时，独占输出可能仍不可读。v4保存local_wait_error并以空local_wait表占位；空表不能作为条件满足证据。不重读而丢掉原始部分结果，旧格式保持。

人工例执行实际网络接收→本地复制到另一地址→后继put读取输出→另一GPU复制并回传；仅改变初始字节会改变实际输出及后继包。验证排队期间输入/输出保护、源多读、目标提前读取/覆写、本地reset冲突、完成后合法复用、零时长、截止、重复/id改名、无copy v4与v3一致、旧全部流程及原生绕过CLI的拒绝。独立字节oracle重放实际包与本地读取/输出，不能只比较两份报告字段。保护核心得到独立区间枚举。

每构建/检查命令60秒、RSS1GiB/地址2GiB/阶段64MiB，仿真5ms/结果16MiB；累计480秒/192MiB，检查点成员128MiB/压缩48MiB，内存盘预留8GiB。原始件`/dev/shm/moe-local-io-20261023/`仅易失、无持久备份。只增量编译受影响平台对象并保留旧runtime；不安装下载、改固定依赖、运行GPU或重跑旧失败千卡/Prefill。功能平台与研究分别验收，只本地提交，不push。完整现代provider/self-peer/可见性和校准仍缺，论文主贡献未成立，目标保持in_progress。
