# MoE 在途请求恢复：创新筛选与安全边界

本目录先检验恢复候选，尚未实现恢复优化器。研究假设、故障／安全模型、竞争解释和淘汰条件见[预登记方法](protocol.md)，面向人的结论见[研究报告](../../docs/reviews/创新点筛选与MoE安全恢复候选_20261023.md)和[HTML](../../docs/review_artifacts/recovery_safety_20261023/report.html)。

`check_model.py`枚举同QP有序、跨QP可交错的有限事件，检查新轮消费和消费后的旧写。10个条件有部分等价，不能当独立性能样本；枚举数量不是故障概率。模型不调用平台／测试／GPU，不模拟时延或专家数值，不证明真实DeepEP漏洞。

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/recovery_safety/check_model.py --output /dev/shm/NEW_RECOVERY_MODEL
PYTHONDONTWRITEBYTECODE=1 python3 experiments/recovery_safety/verify.py \
  --summary /dev/shm/NEW_RECOVERY_MODEL/summary.json --output /dev/shm/NEW_RECOVERY_CHECK.json
```

`verify.py`用子集动态规划独立核对合法顺序数量，用最后一次写入判定独立回放反例，并拒绝删顺序／改消费值两种篡改。它不调用枚举器的回放函数；这仍不是实现形式化证明。

`collect_sources.py --output NEW_DIR [--phase initial|followup|targeted]`只读现有固定源码及少量公开资料，每阶段60秒；单份2MiB、全部阶段累计12MiB。搜索结果仅作为选读线索，跳转页不作正文证据。固定NCCL/DeepEP的树、blob、SHA256与目标原文分别保存；不是更新依赖。原文快照位于运行目录，Git中只存必要摘要、位置和身份。

`build_review.py --run RUN_ROOT --output NEW_REVIEW_DIR`从上述保存结果构建摘要、来源和HTML，不联网。`checkpoint.py`负责有界归档和新源码根／新cwd离线复现，具体命令及证据见版本化的[归档](../../docs/review_artifacts/recovery_safety_20261023/archive.json)与[恢复](../../docs/review_artifacts/recovery_safety_20261023/recovery.json)。离线恢复不重新获取外部文献、不跑网络或GPU，依赖宿主Python标准库。

本轮原件`/dev/shm/moe-recovery-safety-20261023/`为易失存储，无持久备份。单阶段60秒/RSS1GiB/地址2GiB/64MiB，总180秒/128MiB、tmpfs保留8GiB。不下载依赖／模型、不执行旧失败Prefill／1024、不改平台和依赖、不push。论文主贡献尚未成立。
