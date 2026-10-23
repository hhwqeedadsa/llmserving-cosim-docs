# 在途恢复是否值得做

本目录使用原始专家选择记录，核算当前 MoE 层保留有效结果的**条件工作量上界**，并检查专家迁移后重复累计的抽象风险。没有执行真实通信库恢复、GPU或包级网络，不预测时延。研究方法与淘汰条件见[protocol.md](protocol.md)，结果见[报告](../../docs/reviews/MoE在途恢复价值与状态存活判别_20261023.md)。

在仓库根目录运行，`NEW_*`须是不存在的输出路径：

```bash
python3 -B experiments/recovery_value/account.py \
  --repo "$PWD" \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --output /dev/shm/NEW_RECOVERY_ACCOUNT
python3 -B experiments/recovery_value/verify.py \
  --repo "$PWD" \
  --manifest experiments/moe_trace_hotspots/results/four_requests_20261018/manifest.json \
  --account /dev/shm/NEW_RECOVERY_ACCOUNT --output /dev/shm/NEW_RECOVERY_CHECK.json
python3 -B experiments/recovery_value/contribution_identity.py \
  --output /dev/shm/NEW_CONTRIBUTION_IDENTITY.json
```

前两条依赖清单指定的四份用户已下载JSON（合计不超过16MiB）；缺失或哈希不符会失败，不自动联网。最后一条是固定Top8/四owner的小型符号模型，不需数据；其36种重放置不是生产样本，正确性由必需贡献的多重集判定，不证明浮点逐位等价。

`account.py`生成`cases.csv`、`summary.json`和`inputs.json`。每行是一个人为对齐窗口、一个失败owner、一种源存活条件。`whole_layer_tasks`、`affected_token_replay_tasks`和`missing_owner_tasks`是三种重做粒度的token—专家任务数，不是时间。`healthy_vectors_on_affected_tokens`按旧owner局部归约，一份隐藏向量可代表多位专家；假设所有健康部分都完成且仍可复用，实际保留收益可以为零。跨行累计不是同时占用内存。

`verify.py`独立按四个专家ID区间扫描原始数据，不导入分析器，检查59,392行及汇总，拒绝删除案例、增加保留量和误算失效源三种篡改。它不独立证明符号模型或真实库行为。

`collect_sources.py --repo "$PWD" --output NEW_DIR`重新核固定本地源码的SHA256/blob/tree，只读两份官方文档。`run_stage.py --run RUN_ROOT --name UNIQUE -- COMMAND ...`保存命令、cwd、退出状态和资源记录，最多60秒/阶段及128MiB累计，保守预留资料采集60秒；子程序地址上限2GiB，阶段RSS上限1GiB。运行目录须预先建立，位于有8GiB余量的临时卷。已存在记录不会覆盖。

`reproduce.py --repo "$PWD" --run RUN_ROOT --output NEW_RECOVERY_DIR`要求运行目录已有`account/`、`sources/`、`verification.json`和`contribution-identity.json`。它将准确的源码和输入归档、检查成员哈希，然后在新源码根和空cwd执行上述三命令，比对五份确定性产物；不重新联网或执行GPU。归档包含数据原件，仅作本地恢复，来源使用条件仍以原数据集为准，不提供额外授权。

`build_review.py --repo "$PWD" --run RUN_ROOT --output NEW_REVIEW_DIR`从保存结果和版本化Markdown离线生成HTML及小型证据；要求最终恢复记录位于`RUN_ROOT/final/reproduction.json`。不安装Markdown依赖，不执行文档中的命令。

原件`/dev/shm/moe-recovery-value-20261023/`为易失存储，尚无持久备份。论文创新仍待验证，分析器不是已交付的生产恢复接口。
