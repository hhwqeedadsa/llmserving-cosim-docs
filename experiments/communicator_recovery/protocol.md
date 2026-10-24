# 通信域恢复与外部GIN内核

平台固定`917e42d`，NCCL固定`12df1a11afad322be5a204a2db890161cbf8131d`，DeepEP固定`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。本研究是继续实现provider退出前的适用性门槛，不预设发现漏洞或有恢复收益。

## 问题与竞争解释

1. `ncclCommRevoke`是否已经给出本研究需要的通信域排空？检查公开定义、真实入口/异步工作/完成对象，区别调用返回、async状态和资源释放。
2. 该保证是否自动覆盖用户CUDA内核与外部GIN devComm？核对内部/外部创建、abortFlag、GIN等待和DeepEP实际调用。若并未证明，不能把CPU通信域静止等同所有用户生产者停止，也不能反向声称真实库故障或API失效。
3. Shrink/Abort是否保留已有注册、devComm或调用新的完整初始化？核对明确分支；未读到的完整provider/DMA保证保持未知。没有匹配硬件成本时不比较性能。

## 方法与验收

优先复用固定全文缓存及Git commit/tree/blob；新增最多6次1MiB/8秒的固定blob原文读取，不重试或换版本。每份全文记录长度、Git blob与SHA256，摘录保存准确行号和哈希。CPU执行仅精确提取可独立验证的原函数或原文代码块，其余调用记录替身；不安装/构建/运行NCCL、DeepEP、CUDA、驱动或新的网络。

读到控制流程后再登记有意义的合成条件和独立预期，不机械枚举不存在的状态。验证原函数未改、调用/返回与原文相符，拒绝足以改变研究结论的篡改。它不能证明真实失败概率、合法故障并发、用户内核可终止、DMA退出或性能。

输出：版本化来源/摘录、可复现CPU检查和独立oracle（若函数适合）、中文研究结论及下一平台门槛。新源码根/空cwd复算，保存原文、失败、命令、退出状态、hash和累计预算到持久检查点。平台保持固定且研究独立提交；目标仍为有实质创新/强基线/代表性负载/校准及规模证据的网络系统论文，不以源码核查收尾整体目标。

预算见当前任务：每命令60秒/1GiB/地址2GiB/产物16MiB；阶段300监控秒/64MiB，含原件、恢复和持久副本，预留tmpfs8GiB/根盘64MiB。不抬限或盲重试。

## 读到源码后登记的CPU判别条件

共208条合成记录：84条Revoke异步worker（child flag、proxy对象/线程、旧引用和7个成功/失败位置）；20条Shrink（默认/abort、child flag、5个失败位置）；32条父子资源共享真值表；72条GIN wait（内部/外部abort指针、abort值、无deadline/4/10002个合成clock单位、空请求、CQ立即/两次后/不提供成功）。执行精确`setCommAbortFlags`、`commRevokeAsync`、`ncclCommShrink`、GDAKI`waitImplCore`和utility的CPU版`testAbort`，另执行原文的abort指针选择及shareResources表达式。

CUDA名、stream/proxy/child-init/CQ/clock/fence均为记录替身；CPU版abort逻辑和GPU atomic版均按10000次轮询检查，不能把它换算为硬件耗时。无deadline且无abort指针的阻塞下层替身在没有立即提供完成时抛出记录停止；无完成/无有效abort的poll替身在20001次停止。这只是有界检查，不是GPU卡死证据，也不把停止解释成原函数返回。Revoke异步worker检查不是整个公开API、异步调度或资源回收执行。
