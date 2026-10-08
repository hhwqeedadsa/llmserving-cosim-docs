# 运行新增端侧状态的观测对照

这是同一 Scale-Up 实验中对新增观测的信息比较。输入、点采样、规则与评价边界见[对照方法](send-state-method.md)。只使用仓库参考配置和公开平台接口，不依赖旧运行目录、平台测试 fixture 或真实路由账户。

## 从新目录运行

先按[用户指南](../../docs/user_guide.md)构建包含端侧状态采集的当前后端。以下路径相对仓库根目录，每个输出目录须不存在：

```bash
sim_python=.venv/llmservingsim-native/bin/python
sim_backend=build/m17-send-state/output/cosim/ns3.44-cosim-service
study_root=runs/scaleup_observability/my-send-state-study

"$sim_python" -m experiments.scaleup_observability prepare-state-source --output "$study_root/source"
"$sim_python" -m experiments.scaleup_observability preflight --batch "$study_root/source" --output "$study_root/preflight.json"
"$sim_python" -m experiments.scaleup_observability run-batch --batch "$study_root/source" --backend "$sim_backend"
"$sim_python" -m experiments.scaleup_observability prepare-state-observations \
  --sources "$study_root/source/sources.json" --output "$study_root/bundle"

for condition in counts counts-queues; do
  "$sim_python" -m experiments.scaleup_observability measure-observations \
    --observed "$study_root/bundle/observed/$condition" --output "$study_root/$condition"
  "$sim_python" -m experiments.scaleup_observability evaluate-state-observations \
    --truth "$study_root/bundle/truth" --predictions "$study_root/$condition" \
    --output "$study_root/evaluation-$condition"
done

"$sim_python" -m experiments.scaleup_observability measure-state-observations \
  --observed "$study_root/bundle/observed/counts-queues-send-state" --output "$study_root/counts-queues-send-state"
"$sim_python" -m experiments.scaleup_observability evaluate-state-observations \
  --truth "$study_root/bundle/truth" --predictions "$study_root/counts-queues-send-state" \
  --output "$study_root/evaluation-counts-queues-send-state"
```

`sim_backend` 可换成自选构建目录，包括 `build/default`；所选后端必须实现 `observation-config/4` 的实际状态记录。命令名未增加新的平台阶段，`m17-send-state` 只是既有本地构建目录名。

`prepare-state-source` 固定生成 seed=3 参照与 seed=4 新复核输入，每组各单槽/双槽，总共四次运行。配置、摘要、方法版本在执行前保存。`run-batch` 串行运行并生成执行概览；仅能复用已成功且校验一致的运行，失败不会自动重跑。重新准备或分析使用新输出路径，通常不必再次仿真。

## 输入与输出

| 位置 | 用途 |
|---|---|
| `source/inputs/`、`source/batch.json` | 正式实验输入、固定方法与文件摘要 |
| `source/runs/<case>/run/`、`overview/` | 真实执行、公开平台记录及研究者内部概览 |
| `bundle/observed/counts/` | 仅计数与静态拓扑，使用既有测量程序 |
| `bundle/observed/counts-queues/` | 计数、队列与相同拓扑，使用既有测量程序 |
| `bundle/observed/counts-queues-send-state/` | 再加每个窗口左边界的 8 个端点计数快照 |
| `bundle/truth/` | 独立重建的等待区间标签、采样点状态、来源绑定，仅供评价 |
| 各条件的 `predictions.jsonl`、`summary.json` | 所选窗口、状态、输入摘要与处理成本 |
| `evaluation-*/evaluation.json` | 每个输入/槽数/网格及按参照/复核分组的结果 |
| 各阶段 `commands/` | 实际命令、退出状态、日志和资源监控 |

C2 公开包仍只包含 `manifest.json`、`windows.jsonl`。每窗的 `ports` 与原队列条件相同；`rank_states` 按 `rank_ids` 排列，每个向量依次是 `ready_send_count`、`active_send_count`、`send_slot_capacity`，均为采样点的非负整数。公开包没有节点身份、真实结束时间、内部标签或源运行路径。可以只复制该公开目录给测量程序。

C0/C1 的预测保留整窗一个候选值、所有端点状态未确定；C2 的 `ranks` 为每 rank 一项 `selected` 和 `state`。容量未配置记为 `undetermined`；没有待发任务不表示计算端点空闲。具体状态解释见方法说明。

评价的 `point_accuracy_when_classified` 只描述采样点；`wait_coverage_fraction` 和 `missed_wait_rank_ns` 描述所选窗口是否覆盖实际等待。`held_state_agreement_fraction` 是假设采样状态保持到下次采样时的时间一致比例，不是直接记录了整窗状态。两类结果须一起阅读，不能用点判断正确率代替连续覆盖率。其他范围、误选和成本字段沿用[基础观测操作](observations-usage.md)。

## 资源与验证

累计目录由 `common.py` 统一登记，包括已有实验和发送状态支撑产物。四个新场景预留共 3 GiB，实际累计仍不超过 48 GiB；8 GiB RSS、12 GiB 地址空间、每阶段 1800 秒和 20 GiB 空闲不变。命令失败时保留原因，不自动提限、删除旧产物或重跑。

无需后端的小型检查：

```bash
"$sim_python" -m unittest discover -s experiments/scaleup_observability/tests -v
```

检查覆盖同刻状态顺序、禁止读取未来更新、短等待漏采、点判断与时间覆盖的手算、原基线指标一致、四个新输入的配对，以及观测缺项和额外字段拒绝。正式研究结论只来自上述独立运行流程。
