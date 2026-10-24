# 显式self-peer服务验收

平台基线`7549219`，规范见[任务定义](../../docs/tasks/self_peer_service_spec.md)，普通用户入口见[混合服务示例](../../examples/scaleout_self_peer/README.md)。`audit.py`独立解码小输入的MRT1帧、实际端点TX/RX/ACK及内存字节，核对self FIFO、服务时刻、原子旧值、source lease和后继；不作为任意v5程序或完整现代GIN的通用oracle。

`stage.py`负责固定资源及最多4次网络，`build.py`只增量编译三个消费者并链接两入口；`checks.py`保留非法输入和证据篡改。`checkpoint.py`先核安全路径及全体成员hash再恢复，`recover.py`在新Python根/空cwd中离线核对原两组结果，再运行独立的零时长/前缀发布/源字节self小例。运行脚本先检查其内容和预算，不对已完成网络重复执行。

完整命令、两次构建准备失败、运行时与固定库hash保存在`runs/self_peer_service_checkpoint_20261024/`检查点及原始阶段目录`/dev/shm/moe-self-peer-service-20261024/`。本目录验收收据与中文报告在阶段收尾后保存；状态以[当前任务](../../docs/tasks/current.md)为准。

这里只证明功能self服务与实际远端网络共存，不证明真实loopback、NIC竞争、GPU可见性、现代DeepEP完整执行或论文创新。context规划器仍拒绝self。
