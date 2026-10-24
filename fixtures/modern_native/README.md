# 现代完整操作与实际网络回调：进行中的验收

规范见[平台任务](../../docs/tasks/modern_native_bridge_spec.md)，用户入口见[公开示例](../../examples/modern_deepep_native/README.md)。本阶段仍`in_progress`：两rank两调用正常/真实丢包已完成；四份真实路由的一次四rank调用触发原生256次控制交互上限，未完成Combine。不能将部分通过写成整个阶段验收通过。

`audit.py`不导入新增controller/layout，独立重建必要操作、动态slot/通道、实际MRT1/ACK/源读取、local copy、计数、self原子和完整内存。Go-Back-N验收按连续接收前缀推进；缺口之后提前到达的包被丢弃，不当作已缓存。oracle只覆盖本阶段功能数据和声明模式，不是通用GIN或GPU内存序证明。

`checks.py`保存篡改/公开拒绝及真实失败的限制分析；`prepare.py`核对原有四请求/hash和固定session运行时，没有新下载。`stage.py`限制最多4次网络及600秒/384MiB，单命令60秒/1GiB/地址2GiB/阶段128MiB；失败保留，不自动扩限。`checkpoint.py`及`recover.py`负责安全恢复、新源码根/空cwd和原定第四个独立小网络，不重跑前面的成功网络。

原始命令、失败结果、运行时与输入在`runs/modern_native_checkpoint_20261024/`检查点及`/dev/shm/moe-modern-native-20261024/`。版本化收据在本目录，完整进度以[当前任务](../../docs/tasks/current.md)为准。后续需要保留完整回调历史与正确就绪时刻的依赖驱动唤醒，不能通过推迟独立工作或静默提高计数上限制造成功。
