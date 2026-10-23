# 接收窗口与本地处理的因果事务执行

从干净`2062690`继续；上一轮可见性研究已经收尾。本轮只做平台增量，实验/性能研究另立阶段。原生MRT1事务入口增加独立`moe-rdma-transactions/2`：operations显式区分remote和local，后者含rank、duration_ns、after及可选local_wait；根local_model固定serial_per_gpu/1、duration_source=declared_ns。每GPU一个非抢占FIFO槽，实际前驱及本GPU收到的窗口值同时满足后入队；入队时一次检查并消费/reset所有条件，排队不占网络工作槽，完成事件才解锁后续事务。它不验证GPU内存序、输入buffer租约或真实算力。

全部工作共用一个原生ns-3时钟、窗口、持久QP和队列。remote依赖仍为applied/request_acked/completed；local后继只允许completed。local不允许connection/verb/queue_fence/绝对ready时间；按输入序号打破同一progress中入队平局，旧排队工作优先于新解锁工作；不同GPU可以重叠。零时长仍经真实Schedule事件完成；应用尾部本地处理没有结束时不能提前关闭网络；截止输出已发生前缀，不能用计划时长填充完成。

v1及旧字段/行为保持，v2至多32个remote+16个local，总≤48；至少一个remote以保留入口用途。local_model只在v2可用，新增输出只在v2出现。CLI和原生入口都拒绝非法GPU、布尔/负/超界时长、未知kind、跨kind事件、额外字段及无效local_wait。明确没有从远端truth获取输入就绪；MRT1 applied/local_wait仍不是GPU visible；不接现代Python计划、不定义self-peer代价，也不改通信协议。

独立公开流程：双源向目标传真实数据/计数，目标局部条件消费后处理，再发后续轮通知。由实际源payload、目的端包、解码内存/消费order、请求ACK和原子返回核查入队与完成时间；覆盖同GPU排队、跨GPU重叠、零时长、旧队列优先、只本地尾部、错误计数/数据保持等待，以及5ms截止不偷发。重复/id改名不扰动线上数据；无local的v2与v1相比仅新增说明字段。旧v1工作、窗口等待和queue_fence全部以旧/新二进制逐字节及原工作流回归核验。

只增量编译transaction_probe和remote_transactions，复用只读锁定原生ns-3库与既有fabric/persistent对象，全部旧binary逐hash保留。不安装/联网/改依赖、不执行GPU、不重跑旧失败Prefill/1024。每构建或检查命令60秒、RSS1GiB/地址2GiB/阶段64MiB；仿真5ms/终态16MiB，累计受监控480秒/192MiB，归档成员128MiB/压缩48MiB，内存盘预留8GiB。原始件`/dev/shm/moe-transaction-stages-20261023/`仅易失无持久备份，根卷不足8GiB只写源码和小报告。阶段失败保留，不抬限重试；工具/实现缺陷先记录修复，只有新证据需要时重核受影响项。交付可复用例/检查、来源边界、HTML与本地提交，不push；论文主贡献和最终目标仍未成立。
