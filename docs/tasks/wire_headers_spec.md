# 有界报文头观测

状态：平台已验收。从`7a0c9fd`继续，为逐QP ACK关联提供实际线上IPv4/端口字段；不改变包、调度、仿真时钟或选路。结果与命令见[独立验收](../../fixtures/wire_headers/README.md)，后续研究另行提交。

输入：已有可选`packet_capture`新增可选严格布尔`wire_headers`，默认关闭。输出：仅开启时在`packet_capture.wire_headers`提供按完整头元组去重、首次保存事件顺序的headers/columns表；event_header_indices与实际保存events一一对应。UID是ns-3 lineage，不能当全局唯一发送；同UID不同头可有多个表项，靠事件索引区分。表只覆盖实际保存的capture事件，受同一时间/节点/事件数量限制，不承诺窗口外包或被省略事件的头信息。

字段：packet_uid、IPv4源/目的（32位数值）、ip_protocol、source_port、destination_port、ports_present。UDP数据17从UDP头读端口，ACK252/NACK253从原生qbb ACK头读端口；其他协议ports_present=0、两个端口为0，不凭字段名虚构TCP/RoCE解析。表是评价资料，策略不能读取；不加入内部stream标签或修改数据包。

验收：多QP同端点/相同累计序号仍可按实际反向四元组区分；数据/ACK头及偏移来自固定原生定义。关闭时与旧二进制结果逐字节一致；开启后移除新增字段和selection开关，原结果一致。检查截断、时间/节点选择、非法布尔，以及旧本地/事务路径受影响范围。只构建本任务相关独立原生二进制，所有Fabric消费者对象使用一致的新头；旧二进制、对象和固定依赖保持。

预算：平台原始件`/dev/shm/moe-wire-headers-20261023/`，每构建/检查60秒、RSS1GiB/地址2GiB、单阶段64MiB，总480秒/128MiB，仿真5ms/结果16MiB；归档成员96MiB/压缩32MiB及卷预留8GiB。不安装下载、改依赖、执行GPU或重试旧失败。后续研究另行登记、提交和验收，不能把观测开关称为网络性能优化。
