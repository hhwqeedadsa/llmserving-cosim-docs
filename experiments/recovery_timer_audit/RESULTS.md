# 恢复计时与调度机会核查结果

本轮没有新网络、构建或协议修改。读取四份已验收的128端点Clos网络原始记录，逐包重建源端恢复批次；源码审计补充TCP/RXE与当前模型的差别。整体研究目标仍在进行，本页仅总结本次审计。

- **计时重启有现成依据，不能称为新漏洞。** RFC6298是TCP资料；Linux v6.12软件RoCE的计时状态机、PSN和时钟舍入均另有条件。仿真初始17µs不能直接作为硬件参数。
- **源NIC的批次竞争存在，但当前没有任务收益证据。** 两个prefix条件各12次恢复首包立即发出；两条完整批次分别受另一QP占线181ns和110ns。其消息在原集合门限前仍有5.431–7.435µs余量。该比较削弱本场景“到期后恢复发送优先级”的开发依据，不是反事实收益上界，也不排除共享交换机调度。
- **ACK节奏是外推硬件的具体缺项。** 当前持久适配器强制逐包ACK，选择接收路径逐包生成反馈；RXE普通成功RC SEND/WRITE依请求位发送ACK。真实专家选择文件不包含WQE拆分和网卡ACK规则。条件两包WRITE仅解释差别，不是目标DeepEP或mlx5实测。

优先补齐目标通信库与操作的ACK/WQE语义，再决定是否研究尾部检测。任务进度感知的共享网络服务仍是待验证候选，需要与同信息的FIFO、流大小/年龄或coflow方法比较，不能把成熟绕行、选择重传或改变参考ACK规则的收益算成创新。

## 交付与证据

- [网页](results/20261030/report.html)、[完整报告](results/20261030/README.md)。
- [源端逐批次记录](results/20261030/analysis.json)、[独立缺口和物理TX核验](results/20261030/verification.json)。
- [批次交错与门限](results/20261030/competition_explanation.md)、[实际记录](results/20261030/competition.json)。
- [ACK条件解释](results/20261030/ack_policy_explanation.md)、[逐段源码证据](results/20261030/ack_policy.json)、[来源目录](results/20261030/sources.json)。
- [执行与产物校验](results/20261030/receipt.json)：包含失败阶段、事后脚本身份和补充来源，不以当前收尾脚本替换原冻结方法。

## 复核方式

工作目录为仓库根。网络原件路径与SHA256见`runs/recovery_timer_audit_20261030/input/frozen.json`；方法提交为`006f8f1ee0`。该轮prepare/analyze/verify/publish命令保存在对应`monitors/*/probe.json`的`command`字段，源码调用参数见各程序`--help`；公开响应需自行提供，网络原件和完整来源正文不随Git发布。

事后批次分析实际运行：

```bash
.venv/frontend/bin/python -B experiments/recovery_timer_audit/monitor.py \
  --root runs/recovery_timer_audit_20261030 --stage competition-v2 -- \
  .venv/frontend/bin/python -B experiments/recovery_timer_audit/postrun/explain_competition.py \
  --stage runs/recovery_timer_audit_20261030 \
  --delivery experiments/recovery_timer_audit/results/20261030
```

ACK规则补充分析读取此前已取得的响应，不发HTTP请求：

```bash
.venv/frontend/bin/python -B experiments/recovery_timer_audit/monitor.py \
  --root runs/recovery_timer_audit_20261030 --stage ack-policy -- \
  .venv/frontend/bin/python -B experiments/recovery_timer_audit/postrun/ack_policy.py \
  --stage runs/recovery_timer_audit_20261030 \
  --delivery experiments/recovery_timer_audit/results/20261030 \
  --response /tmp/moe-rxe-resp-response-20261030.json
```

上述阶段会拒绝覆盖已有结果，复现应使用独立的新根和输出目录，并按prepare→analyze→verify→publish→批次解释→ACK解释顺序执行。ACK解释保留修改前报告与收据；收尾程序另用`--stage`和`--delivery`校验全部文件后生成新收据。原冻结`closeout.py`只接受全阶段成功；本轮有一项报告程序变量遮蔽失败，实际用`postrun/finalize.py`核验该唯一已知失败及修正源码，保留失败耗时。该收尾程序针对本轮记录，不能直接当作新运行的通用验收器。

独立核查拒绝三类统计篡改：缺口字节、首发时间、其他QP占线。首次competition分析在写结果前失败；修正报告程序后运行competition-v2，无网络重跑。公开资料合计6次HTTP请求（含一次超时），声明超时总上界58秒；原清单的5次/53秒保留不改，补充来源单列。网页只验证静态资源和链接，不声称浏览器渲染；硬件条目和DOWN端口不构成RoCE校准。

最终审阅将说明性WRITE算例改为6,000B/4,096B路径MTU，避免把模型9,000B数据片长带入RXE假设。原说明、驱动和收据已保留，修订程序身份单列在最终收据；只修改解释性算例，不改变任何网络原件、批次统计或收益结论。当前`ack_policy.py`在新目录生成的已是修正算例。
