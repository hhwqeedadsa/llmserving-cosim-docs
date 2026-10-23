# 事务准备与前缀发布

目标：把已有GinWorkQueue的准备/发布边界接到实际MRT1网络，避免未来现代适配时将AggregateRequests误成逐操作发包或合并请求。依据固定NCCL 12df1a11afad322be5a204a2db890161cbf8131d的GIN→SKIP_DB_RINGING映射和DB/DB_NO_DBR分支；仅实现显式前缀功能规则，不是CPU_PROXY_FREE_FLOW、物理MMIO次数或GDAKI逐位成本模型。

范围：transactions/5可选根submission_model={mode:explicit_prefix/1}。启用后每个remote节点必须声明bool aggregate_requests；true只准备，false准备后发布同一连接至自身ticket的完整已准备前缀。源范围仍从提交保护到事务完成，按既有source_model在实际发布时读取字节，准备只处理描述符，不声称GPU DMA读取时刻。不得发布其他连接、吞掉操作、重新创建QP或跳过真实ACK/原子回复。未启用模式及v1—4行为/结果保持。

记录：启用模式增加prepared_ns及prepared回调，允许after引用该实际事件；transactions.submission保存模型和逻辑前缀发布记录。记录不作为额外调度事件，不当作门铃物理写次数。发布、目标应用、源完成与GPU可见仍分开。全部聚合的尾部不自动ring；信用不足阻塞、错误依赖或尾部未发布在真实5ms截止保留前缀，不补完成、不自动增深度。已有queue_fence只等待调用时已保留前缀，不替应用补发布；这不覆盖全部GIN flush分支。

独立验收：同连接延迟非聚合提交前无包，正确前缀逐项发出；另一连接不能解锁；深度1与未结束聚合尾保存未完成；prepared回调实际触发在线后继；等待ACK保留源保护；只改初值改变实际包；非聚合全部false时去新增字段后与旧结果完全相等。独立按实际包和事件核对，不以测试脚本退出0代替字节/状态检查。两条入口、非法类型/旧schema拒绝、稀疏/事务受影响回归分别检查。

预算：/dev/shm/moe-submission-prefix-20261023/；每构建/检查60秒、RSS1GiB/地址2GiB、阶段64MiB，仿真5ms/结果16MiB；全阶段480秒/192MiB，归档成员96MiB/压缩32MiB、tmpfs保留8GiB。只用已有固定工具链/原生库隔离构建，不下载安装/改依赖/GPU/旧失败Prefill或1024重跑；失败保留，不自动抬限。大件tmpfs易失无持久备份，小源码/摘要入Git，仅本地提交不push。现代self服务、GPU可见性、真实张量/专家计算、校准与千卡完整执行仍未完成，不把本功能或参数变化作为论文收益。
