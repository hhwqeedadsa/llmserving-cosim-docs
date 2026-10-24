# Context连接绑定功能验收

本组验证显式通信域/source/context/peer身份与MRT1持久连接、物理NIC及实际端点包的对应。普通用户从[公开示例](../../examples/scaleout_context_transactions/README.md)运行；[规范](../../docs/tasks/context_transaction_binding_spec.md)定义边界及预算。本组不是论文性能实验，不是完整现代GIN。

`audit.py`独立解码实际源payload、保留重传实例，按完整wire capture核源TX、目的RX和真实ACK；重放目标put/fetch-add及全部4KiB内存，核返回旧值、依赖、本地时长和恰好一次应用。它只接受这组人工inline put/fetch-add及无copy本地阶段，不声称验证所有transactions/5程序。公开check只核连接与完成事实，不替代这个内存oracle。

预定网络只有3次：两NIC健康复用；相同程序首请求真实丢失一次；新源码根/空cwd/包内库的一次独立单put。前两个网络完成后只做离线核查，不为修改checker而重跑。健康/故障各6远端+1本地，4个binding、8个请求/响应载体；原子返回17、最终值20。故障连接恢复时其他3个binding已完成。

`stage.py`保存逐命令的cwd/参数/状态，并通过公开宿主监控限制每命令60秒、RSS 1GiB、地址2GiB、阶段96MiB；累计450受监控秒、256MiB新存储，含嵌套监控、全部副本和1MiB收据预留，管理另留10秒。网络1ms/result16MiB/capture20000，根盘64MiB/tmpfs8GiB预留。归档放`runs/context_transactions_checkpoint_20261024/`，恢复放阶段的`restored/`；复制的历史监控不重复算执行。

`checkpoint.py`在写入恢复目录前核路径、类型、配额、成员hash；`recover.py`使用恢复Python、空cwd和包内runtime完成离线重算及第三次网络。二进制/8库不重编，launcher显式绕过历史ELF RPATH，再由真实`loaded_libraries`核包内路径。包绑定本机Linux x86-64/glibc ABI，不是从零构建或跨主机镜像。首次健康执行实际加载旧位置的同hash库，保留此事实。

版本化摘要为`verification.json`（独立字节/包核验及负例）、`checks.json`（输入角色/派生篡改、帮助与依赖）、`recovery.json`（新根运行）、`archive-safety.json`与`closeout.json`。完整命令、原结果和包在本机运行目录；摘要的成功范围仅限这些功能条件。原`inputs.native_commit`为上游基线常量，实际native HEAD及源码/二进制/库hash由本组`identity.json`单独保存，不将常量当实际HEAD。

self-peer、真实GIN/provider服务、GPU可见性、QoS时延、硬件校准、独立现代负载和千卡完整推理仍缺。不同context的局部恢复安全也不等于生产通信域恢复安全；不能从这些小例完成时间推断现代MoE性能或论文贡献。
