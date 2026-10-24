# 选择重传的事务字节交付验收

基线`aff6f9a`。本阶段补齐实际乱序Packet到MRT1解码器的有序交付，使已有选择重传强基线可用于地址写、原子回复及在线事务。没有新增恢复算法，不属于性能收益研究。公开配置见[事务指南](../../examples/scaleout_rdma_transactions/README.md#选择重传下执行实际事务)，约束见[阶段规范](../../docs/tasks/transaction_selective_payload_spec.md)。

`prepare.py`生成五个人工功能条件和固定8GPU标签Clos拓扑；不是GPU执行或MoE请求。`build.py`增量编译修改的载体和三个入口，复用固定Fabric静态库及八个原生ns-3库，不能当作从零构建。`run.py`执行以下前七次网络；`replay.py`在新源码根/空cwd执行第八次及离线复核。`reject.py`检查两个原生入口的纯准入拒绝，不运行网络。

| 条件 | 核验内容 | 已有结果 |
|---|---|---|
| request-gap | 八个原子请求，第一个40B请求丢失；后续缓存不提前执行 | 恰好八次目标应用，返回旧值7/8/10/13/17/22/28/35，最终43；乱序峰值280B |
| reply-tail | 最后一条40B原子回复丢失 | 真实98,164ns超时后恢复；不能仅凭请求ACK完成最后原子 |
| chain | 2600B写入中间包丢失，计数等待/清零、本地copy和转发 | 五远端/三copy，实际缓存632B，十二源包/6464B（含重传，不含包头/ACK） |
| explicit | 准备与发布分开 | 0ns准备，20,000ns才发布及采集源字节；两事务分别保留 |
| online | 原子实际返回17才构造新任务的117标记 | 初始native操作为空，控制流/回调及实际线上字节一致 |
| default-old/new | 同固定八库下完整缺省结果比较 | 仅规范化已核hash的库路径后相等 |
| fresh byte SR | 新源码根/空cwd运行无payload receiver的旧选择重传输入 | 结果与纯字节旧参考比较，见recovery.json |

`audit.py`只依赖Python标准库，不导入平台解码器或状态机。从实际源Packet字节和逐物理跳接收/丢弃重建选择接收前缀、缓存、ACK/SACK到达及可交付范围，再独立解码MRT1。按共同order重放源读保护、目标写/原子、小端条件消费、本地复制，并检查最终稀疏页、全部队列与lease排空。输入声明只用于核验操作意图；不会补出缺失的接收字节。七类篡改分别改变交付hex、UID、原子旧值、最终页、ACK时刻、源释放和copy快照，均应拒绝。

离线重算命令（输出文件须为新路径）：

```bash
python3 -B fixtures/transaction_selective_payload/audit.py \
  --stage /path/to/stage --output /path/to/new-verification.json
```

结果入口：[独立核验](verification.json)、[迁移/回归](recovery.json)、[资源与失败收尾](closeout.json)。原始产物本机保存在`runs/transaction_selective_payload_checkpoint_20261024/`；恢复包携带源码、三个可执行程序、固定八库、输入和证据。它是同Linux宿主增量运行包，仍依赖宿主标准系统库。

预算600累计受监控秒/256MiB累计新增存储，单命令60秒/RSS1GiB/地址2GiB/产物64MiB；网络5ms/result16MiB/最多八次。首次离线检查因空前缀拼成`-protocol.json`失败，保留日志后仅修检查器；没有为此重跑网络。

这里没有完整Dispatch—计算—Combine、现代self-peer物理服务、GPU可见性或真实GIN/RoCE成本，也没有千卡执行及硬件校准。纯传输载体`atomic_operations_supported=false`仍正确：原子由MRT1事务层执行，不由载体模拟硬件原子。
