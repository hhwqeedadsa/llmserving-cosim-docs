# NIC唤醒修复后的完整应用

本实验检验源NIC的正确性修复会不会改变上一轮的应用结论。三个新网络已经关闭，旧三个结果保持只读；所有work、mapping及拓扑逐字节相同，协议、计算、故障时刻和16us NACK等待也保持。平台固定验收`eaba713`，原生补丁`68a07f7`；事前范围和宿主预算见[protocol.md](protocol.md)。

修复消除了事前选定work99两种故障条件中“实际已可发送、源端口却空闲”的正长度时间。应用没有加速：健康/延迟改路/立即改路由397.733/446.808/455.794us变为401.735/452.119/463.884us，立即减延迟的差值8.986→11.765us。提前源发送会改变共享排队和实际拥塞反馈，不能从局部正确性推出应用性能单调改善。

还纠正了一处分析方法：某次提速会把`next_avail`移到过去，不能拿更新后的截止反推过去已经可发。`eligibility.py`按真实rate更新分段计算；`verify_eligibility.py`独立核时间边界与源端口物理服务。旧work99的回溯空闲7.496/11.136us，在分段时间语义下为7.273/10.829us；新两个条件均为0。原结果和初版分析不覆盖。

## 复核与边界

- `prepare.py`逐成员核原始研究包，取固定输入与旧结果；不下载数据或执行网络。
- `run.py`只调用公开CLI，检查执行前后程序、八库、平台及原生源码身份。`--resume`仅允许跳过已终态成功且命令相同的网络，失败不改限额或重新执行。执行清单和失败原件保留。
- `analyze.py`覆盖全部76写、全部并列最晚前驱、物理路径/排队和事前固定work99；`verify.py`独立检查原路由/216节点依赖、字节/真实ACK/MSR1、物理FIFO/队列积分、实际enqueue的BFS+hash与10类篡改。
- `eligibility.py`、`verify_eligibility.py`纠正回溯资格统计；后者另拒绝3类篡改。`diagnostics.py`仅作原始发送顺序和声明控制器状态摘要，不声称唯一拥塞根因。
- 本机持久包`runs/nic_wakeup_effect_checkpoint_20261024/evidence.tar.gz`包含研究源码和六份原件。沿用已核验的有界研究归档格式，并增加实验身份；没有native运行时，不是可移植二进制或异机备份。

在已有Python环境中，从仓库根目录离线恢复到尚不存在的目录：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m experiments.nic_wakeup_effect.checkpoint restore \
  --archive runs/nic_wakeup_effect_checkpoint_20261024/evidence.tar.gz \
  --output /tmp/moe-nic-effect-review
```

全部成员安全与hash检查完成才写文件；随后在新研究源码根/空cwd重新生成六份JSON并要求逐字节一致，不启动网络，不读取平台或外部动态库。按协议保留宿主60秒/1GiB RSS/2GiB地址及磁盘限额；恢复命令本身不替代宿主监控。

最终[摘要](results/20261024/summary.json)、[核验](results/20261024/verification.json)、[分段时间核验](results/20261024/eligibility-verification.json)、[新根复算](results/20261024/replay.json)、[预算收尾](results/20261024/closeout.json)及[中文解释](../../docs/reviews/源NIC修复后的完整应用与拥塞反馈_20261024.md)保存结论。四MMLU请求、两层两步和旧贡献复制/声明计算不是现代DeepEP或代表性生产泛化；匹配校准、独立留出、完整千卡与论文创新仍未成立。该实验不自动启动新网络或算法开发。
