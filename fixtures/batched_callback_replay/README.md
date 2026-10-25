# 保留每批时刻的离线交互判别

本轮仅分析已保存的回调，平台和网络不变。规范见[系统前置诊断](../../docs/tasks/batched_callback_replay_spec.md)，解释见[中文报告](../../docs/reviews/保留回调批次时刻的交互判别_20261025.md)。本工具不是公开现代入口的新模式。

`audit.py`先重建全部原控制器状态，再根据当时已经提交的工作生成提交前沿。条件触发前仅积累原始回调批次；条件触发后按各批原`now_ns`逐一处理。任何中间批次本应新增原生工作或finish即否决，不用原命令决定唤醒。原命令仅作检查预期，不把未来回调用于选条件。原控制器的256上限和逻辑步数不变，候选同步往返单列。

| 保存的场景 | 原逻辑批次 | 候选同步往返 | 未触发的尾部批次 |
|---|---:|---:|---:|
| 默认健康 | 213 | 63 | 0 |
| 订阅/计时健康 | 165 | 63 | 0 |
| 订阅丢包 | 171 | 66 | 0 |
| 原四卡失败前缀 | 256 | 60 | 3 |

三组完整记录的全部现代事件、原生任务及提交时刻/内容/顺序、slot/Combine绑定、窗口内存及完整快照相同。四卡条件触发时交付520事件，另3批是原记录的253—255步；仅作明确标注的离线尾部展开后重建526事件，仍不是所需566事件。最大积累52批、紧凑JSON回调内容8,029B是这组前缀的观测值，不是完整调用或生产缓存上限。

`verify.py`不导入控制器或条件生成器，独立核对全部触发、已提交身份、每批时间/边界/顺序、提交摘要、完整快照、尾部标记和计数。14项Python检查（6新、8已有依赖检查）及9类证据篡改拒绝通过；固定13依赖一致。增加批次顺序篡改检查时只重核已有审计报告，没有重跑原始审计来修checker；前一份8类检查报告亦保留。

在仓库根目录使用已有原件可执行（输出必须不存在）：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.batched_callback_replay.audit \
  --source /dev/shm/moe-batched-callback-replay-20261025/original --output /tmp/batched-audit.json
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.batched_callback_replay.verify \
  --source /dev/shm/moe-batched-callback-replay-20261025/original --report /tmp/batched-audit.json \
  --output /tmp/batched-verify.json
```

有界执行使用本目录`stage.py`，不能向此前关闭的阶段追加运行。完整原始报告、四组输入和监控存于`runs/batched_callback_replay_checkpoint_20261025/`的检查点；必要摘要见[核验](verification.json)、[结果](summary.json)、[恢复](recovery.json)、[收尾](closeout.json)。包只含源码和离线证据，不含原生程序。最终包是收尾前快照，末次归档监控和管理收据另存。

边界：现有记录保存的是前端交付批次，不能恢复原生端全部被抑制的Progress边界。下一阶段需要实现明确、有界的原生批次记录和交付规则，区分逻辑处理步与同步往返计数；任何强制容量flush都必须保留所有回调和时刻。先比较小型真实网络的全包/内存/同纳秒顺序，再回原四卡。不能据本轮离线结果宣称真实网络等价、四卡成功、性能收益或论文创新。

独立恢复已经完成：初始包经全成员hash准入后，在复制Python根/空cwd重算审计与9类篡改核验，两报告逐字节一致。它不执行原生网络，平台及固定原生程序/八库的hash保持。最终资源收据与最终归档末态监控单独保存，不继续向本阶段追加原生运行。
