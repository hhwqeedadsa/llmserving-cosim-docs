# 现代direct窗口与源缓冲验收

本增量为固定现代DeepEP direct建立可检查的相对内存位置和源使用期限，供后续接入真实网络完成回调。它不执行网络、CUDA或通信库，不提供性能数字。主窗口不分配实际16MiB字节数组，只保存范围与必要整数状态；provider索引信号独立于应用窗口。

用户入口与Python接口见[现代计划示例](../../examples/modern_deepep_partial_order/README.md#窗口计数与源缓冲检查)。平台代码为[窗口核心](../../python/moe_ub_cosim/modern_deepep_windows.py)，原事件核心和plan/2保持原定义。

## 来源与功能边界

固定DeepEP `f6aa38fe06cd4a0489e7730365d4e0114970a2f3`、NCCL `12df1a11afad322be5a204a2db890161cbf8131d`，12份源码SHA256/Git blob与完整树核对，详见[source_basis.json](source_basis.json)。本轮仅新增读取700B的layout/common/barrier.cuh，一个GitHub API响应1251B；身份/树从既有已核缓存复用，没有依赖更新或大型下载。

| 源码对象 | 本增量采用的关系 |
|---|---|
| EPSignals/EPWorkspaceLayout | send/recv两行按固定最大rank/专家容量跨距分开；expert子区在实际rank计数后 |
| EPBuffer/Context | workspace按2MiB粒度对齐，应用EP区紧随其后，共用注册窗口；各调用复用同一分配 |
| dispatch/combine | 远端token与专家块保留源读范围；本地Dispatch从TMA共享内存复制，本地Combine直接归约写目标 |
| dispatch notify | 已发布的负编码计数经消费重置为0；初值0不表示零token已发布 |
| NCCLGin/NCCL/GDAKI | 信号及shadow按context定位，索引信号由provider独立分配；屏障shadow逐次递增，不按调用清零 |

宿主验证保留原始BarrierSignals/EPSignals字段及方法，仅移除CUDA include、替换限定词并补标准头。严格C++17编译核对8个成员的sizeof/offsetof，workspace结构为15,831,168B，分配取整为16MiB；send/recv rank-expert行分别从24,640/49,216B开始。它是数据布局检查，不是DeepEP/CUDA构建或显存测量。

source read lease表示操作仍可能读取源范围；多读者允许重叠，写入必须等待全部读者的local_complete。epoch只标记版本，不能规避相同owner/offset上的重叠检查。self-peer bulk put保留同样的源读保护；signal不虚构应用源buffer。目标visible不释放源，源local_complete也不自动产生目标visible。

影子计数/信号根据caller宣告的事件和编译操作数更新，不从实际接收字节解码。源写版本在首个相关issue处建立功能检查边界，不代表精确TMA/notify时刻；所有GPU可见性仍由外部声明。故本增量不验证token数值、传输负载、物理地址/key、CQ服务、重试buffer寿命、provider信号物理偏移或self-peer回环代价。

## 可复现检查

先准备固定来源缓存（可从维护记录所列检查点恢复），再在仓库根执行：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 fixtures/modern_windows/check_sources.py \
  --output /dev/shm/my-modern-window-sources
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p 'modern_deepep*_test.py'
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_modern_windows_workflow.py \
  --output /dev/shm/my-modern-window-workflow
```

输出目录须不存在。单命令60秒/RSS1GiB/地址2GiB、新产物≤64MiB、累计≤128MiB、卷预留8GiB；本增量不触发原生网络构建。来源检查需要已有g++；不安装工具。

9项窗口单元检查覆盖跨epoch重叠、多读者、拒绝后状态不变、visible/源完成分离、self计数消费后源仍被读、本地TMA来源及四调用滚动信号；加上原计划/运行时共24项。公开流程执行12条CLI、5次预期拒绝，在全新cwd独立核对224个操作地址、40个self-peer请求、8组计数消费和滚动信号8，并核对两条并发put共享一个源范围；重复及跨目录结果逐字节一致。另保留原计划和事件工作流回归，具体命令与资源见[维护记录](maintenance.json)、[公开流程结果](verification.json)。

来源检查初版把NCCL的tree对象SHA误当成commit SHA而失败，未修改来源或重读网络。更正为核对commit→tree关联及已保存身份请求hash后通过；原失败输出保留在维护记录。测试中的事件由独立功能oracle声明，绝不作为研究负载或网络观测。

## 对论文工作的作用

本增量消除“每轮新内存”“收到即释放源”“信号在数据窗口内”等建模错误的入口，使后续provider接入有明确地址和生命周期约束。它仍是研究基础设施，不能据此宣称现代端到端仿真、网络瓶颈或算法创新。下一步须用真实传输负载和有来源的provider回调驱动这些状态，并单独处理self-peer资源与成本，再研究正常无故障真实路由中的网络等待。
