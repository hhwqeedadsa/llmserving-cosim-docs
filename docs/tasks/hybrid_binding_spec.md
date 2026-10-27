# Hybrid Dispatch 的持久资源绑定

2026-10-27；平台起点 `cdd52b3`。本增量是完整Hybrid联合执行的前置步骤，不以资源绑定或源码接入作为论文创新，也不重新启动已关闭的研究条件。

## 目标和接口

新增公开命令 `scaleout hybrid-deepep-bind --plan DIR --provider FILE --output NEW_DIR`。输入是已有、通过hash和语义重编译准入的Hybrid logical plan，以及独立的provider声明。输出保存原始逻辑输入、provider输入、资源/请求绑定及hash manifest；必须可搬迁并从输入重算，输出存在或输入不合法时不写输出。

本阶段只给出Dispatch数据、rank/expert计数块、周期tail/finish到持久QP的绑定。保留Combine逻辑结果但不绑定其未解析通道；不生成时刻、网络包、GPU成本、最终slot或完整调用。不是可直接交给事务后端执行的程序。后续完整Hybrid功能和两个研究候选仍需继续，验收本增量不等于总目标完成。

## 声明与规则

provider使用 `moe-hybrid-provider-input/1`，逐字段严格检查：

- `devcomm`：1～64字符的资源寿命名字；同一输入的全部调用共享，不能用调用名重建QP。
- `backend=nccl_gin_gdaki`；`nic_handler=gpu_sm_db` 或 `gpu_sm_no_dbr`。不接受AUTO、CPU_PROXY、FREE_FLOW或自动回退。
- `call_contexts/requested_contexts`：1～64且前者不超过后者；`connections`：1～4；按连接数向上对齐分配context。
- `gin_connection_stride`：只能为1或每域GPU数。它表示实际GIN connection配置，不根据requested rail擅自推断；输出同时保留rail peer、world peer和provider peer。
- `local_devices[rank]`：每rank 1～4个不重复非负整数设备ID，依次绑定provider connection。它不是自动发现设备或选择网络路径。
- `limits`：`max_bindings`≤32768，`max_resources`≤8192，输入≤1MiB，输出总字节≤16MiB。严格整数，拒绝bool和未知字段。

原logical输入支持SM=1的抽象门槛示例；固定host在多域/多GPU Hybrid执行时要求SM≥2，新增绑定入口明确拒绝SM=1，旧入口和旧结果保持不变。输入SM/warp是声明的launch形状，不证明某型号GPU的shared-memory容量及真实host自动选择了这个形状。

固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`、NCCL `12df1a11afad322be5a204a2db890161cbf8131d`：

1. 新鲜Dispatch存在notify warp，通知context index为0；data由现有 `qp_mode(..., notify=True)`按SM/warp映射。不要把notify存在与某个data warp是notify混淆。
2. index通过 `connection=index%connections`、`local_context=index//connections`分解；设备取相应connection声明。peer先由rail域索引转换world rank，再按实际GIN connection stride转换provider rank。
3. QP资源键保留devcomm、发送rank、provider connection、local_context、local_device、provider_peer；world目标保留为校验字段。同一持久键不得因call/角色/通道重建。
4. rank-count put带AggregateRequests，expert-count put不带；即使self仍经GIN，二者不能改成本地零成本操作。token远端put带AggregateRequests；本地域token是copy，不分配QP。
5. 远端tail/finish是未聚合GIN signal add，操作数字节8；本域是GPU release atomic，不分配QP。tail值增量按 `finished<<32 | tail` 的有序差计算，精确倍数和空通道均保留独立finish。不会把数据copy与tail atomic变成同一种self服务。
6. 绑定保存来源逻辑ID、call/source/target、channel及SM/warp，token数据保留原Top-K关联；每项控制通知单独标识。保留同通道有序数据及tail前缀，但不臆定多个通道的预约/发布顺序。不预先分配WQE ticket或物理门铃次数。

## 验收

独立人工输入检查：2域×2GPU、E16/Top4/H256、2SM×2warp、两调用；token、计数、空通道与finish全部绑定，世界/rail/provider peer分别核对。一个context让通知/数据及跨通道共享；三个context×两个connection核对取模、向上分配和持久共享；更多context检查SM内独立分配。不得以绑定数降低代表性能收益。

新单元核对同资源跨call稳定、不同peer/设备/context隔离、self路径区别、rank/expert聚合标记、有序tail差值和源逻辑覆盖；错误类型、SM1、未知handler/stride、重复设备、资源/输出预算拒绝。八类以上有意篡改/非法输入至少覆盖provider、逻辑plan、绑定和manifest，必须在写输出前拒绝。

新cwd运行公开CLI，从手写小输入到logical plan再到bindings，并用公开加载接口核验；重复输出和复制源码/空cwd输出逐字节相同。检查旧Hybrid与direct相关单元；不运行旧实验/网络。补用户指南、架构、输出语义和可修改示例。

来源核验增加固定context.cpp、GIN host和peer翻译函数，核本地tree/blob/SHA，不下载执行上游库。检查脚本可读tests/fixture仅作平台验收，研究不能导入它。

## 预算与停止

本轮 `runs/hybrid_binding_20261027/`，累计600受监控秒/128MiB新产物，含复制源码与恢复，另留2MiB/10秒管理；单命令60秒/RSS1GiB/地址2GiB、磁盘留128MiB。零新网络、构建、GPU、下载、安装或依赖修改。先提交规范再实现/检查；失败保留，仅修有定位依据的实现或核验问题，不扩大输入和预算。验收后本地平台提交，不push；研究与平台提交分开，总目标保持进行中。
