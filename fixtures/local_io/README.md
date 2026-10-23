# 本地输入输出保护与网络转发验收

transactions/4的local节点可显式声明copy。实际收到的字节从注册输入区读取、在声明处理时间到期后写入另一输出区，后继put再读取输出。读输入和独占输出的记录从入队持续到完成，覆盖等待本地串行槽的时间。这是字节复制功能，不是专家矩阵运算、GPU DMA或校准时延；没有copy的节点保留旧语义。

## 复核

在仓库根执行，分别指定新程序和保留的v3参考程序，输出目录不能存在，父目录至少预留8GiB：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 \
  tests/integration/check_local_io_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-transaction-probe \
  --reference-backend /absolute/v3/ns3.42-moe-rdma-transaction-probe \
  --output /absolute/new-output-directory
```

普通用户输入和运行命令见[公开示例](../../examples/scaleout_rdma_transactions/README.md#本地处理显式读取输入和写出输出)。检查程序在新cwd调用公开CLI；独立struct解码实际MRT1包，按全部内存事件共同order重放接收、条件消费、源保护和本地读写。输出必须等于实际开始时独立重建的源内容，后继包必须等于运行中实际输出；不是比较两份报告中的hex就判正确。本地源/输出的交集由独立字节集合检查，内存核心另枚举全部2,080个非空子区间。

端点RX/ACK及原子回复匹配只用于固定人工例，要求时刻/端点/前缀唯一；不是一般多流关联器。部分结果只应用目标已实际解码的帧，不补造完成或释放。

## 实际结果

- 最终29条公开CLI、8条原生命令。正常例7个远端事务、4个本地节点（其中2个copy），32个源数据包、1,814B载体负载，8帧实际端点RX/ACK核对通过。结束688,408ns含500,000ns声明本地尾部，不能当作真实硬件成本。
- GPU4的copy在18,138ns入队、100,000ns开始、140,000ns完成；排队期间也保护输入和输出。GPU1继续复制并回传。仅改最初输入第8—256字节，operations完全相同，两次实际copy内容和4次源put负载均随之改变；固定字面量覆写内容保持原配置。
- 8类冲突：排队期输入put、运行期输入put、输出put/原子、提前源读、本地reset、相同输出的另一copy入队、输出条件读。均退出1、保存实际部分结果和原活跃记录，未提前覆写或标记本地完成。两个reset有后一项冲突时，前一项计数也不修改；被拒绝copy不取得部分记录或消费条件。输出条件不可读时pending附local_wait_error，原始错误和部分结果保留。
- 共享同一输入的两次copy可同时持有读记录；后入队者等待FIFO槽后完成。完成后合法复用输入和输出；零时长仍经过调度事件。重复结果逐字节一致，id改名不改变实际包或时间。
- 排队/运行两种5ms截止：分别只保留acquired、或acquired+captured，没有输出commit/release。少计数不取得copy记录，后继不发送。
- v3新旧二进制结果逐字节一致；无copy的v4去掉3个新增事务字段后等于v3。8类非法输入经CLI和原生双重拒绝，不生成结果。
- 内存核心6,758项断言、14项输入检查、5组受影响流程及13个固定依赖检查通过。首版36命令通过后新增“实际运行期覆写”对照，最终37命令通过；两个版本及结果都保留，没有网络失败或抬限重试。一次测试补丁因循环变量顺序不匹配未应用，修正后写入。

[主验证](verification.json)、[回归](regression.json)、[构建资源](build.json)、[中文HTML](../../docs/review_artifacts/local_io_20261023/report.html)、[归档](archive.json)、[恢复](replay.json)、[收尾](closeout.json)。

## 保存、预算和限制

每构建/检查60秒、RSS1GiB/地址2GiB/阶段64MiB，仿真5ms/结果16MiB；累计480秒/192MiB，归档成员128MiB/压缩48MiB，内存盘预留8GiB。原始命令、cwd、退出状态、输入、源码快照、日志和检查点位于`/dev/shm/moe-local-io-20261023/`，仅易失内存盘，**无持久备份**。摘要入Git不等于全部原始数据已持久保存。恢复包内源码、二进制及8个ns-3共享库，仍依赖宿主loader/glibc/libstdc++，不是完整离线依赖构建或跨系统镜像。

输入输出保护仅适用于显式copy模型，不能给未声明IO的节点补保证；入队保护是功能规则，不是实测GPU数据读取区间。实际GPU可见、GIN源安全完成、self-peer物理服务、现代在线事件/provider、专家数值计算和匹配硬件校准仍未接入。没有新千卡成功、真实训练分布或优化收益证据，不能把本增量当作论文主贡献。目标继续in_progress，仅本地提交，不push。
