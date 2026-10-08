# 小场景条件探索操作

使用已固定的实验输入生成条件组合，调用原有运行与概览入口，再检查对照中的实际工作量和执行差异。可选批次如下：

| `--study` | 固定条件与依据 |
|---|---|
| `compute-tempo` | 专家主体计算 0.5/1/2 倍与单槽/双槽，见[计算节奏方法](tempo-method.md) |
| `load-grain` | LOAD 响应载荷 128/512/2048 B 与单槽/双槽，见[拆分方法](load-grain-method.md) |

以下命令从仓库根目录执行。先按[平台指南](../../docs/user_guide.md)准备环境与当前后端，所有输出路径应不存在，不依赖旧运行目录：

```bash
sim_python=.venv/llmservingsim-native/bin/python
sim_backend=build/m17-send-state/output/cosim/ns3.44-cosim-service
study_root=runs/scaleup_observability/my-tempo-study
study_name=compute-tempo

"$sim_python" -m experiments.scaleup_observability prepare-exploration \
  --study "$study_name" --output "$study_root/source"
"$sim_python" -m experiments.scaleup_observability preflight \
  --batch "$study_root/source" --output "$study_root/preflight.json"
"$sim_python" -m experiments.scaleup_observability run-batch \
  --batch "$study_root/source" --backend "$sim_backend"
"$sim_python" -m experiments.scaleup_observability compare-exploration \
  --batch "$study_root/source" --output "$study_root/comparison"
```

`sim_backend` 可替换为自选构建路径；该后端须支持当前原生 STORE/LOAD 和端侧发送状态记录。旧名称只是本机已有构建目录，不是新的平台阶段。

运行 LOAD 拆分批次时，把 `study_name` 改为 `load-grain`，并使用新的 `study_root`。不改变旧目录中的冻结输入。

| 位置 | 用途 |
|---|---|
| `source/batch.json`、`source/inputs/<case>/` | 执行前固定的条件、输入、方法及实现摘要 |
| `source/runs/<case>/run/` | 平台清单、实际执行、公开观测与内部记录 |
| `source/runs/<case>/overview/` | 同口径网络、传输、计算和层完成概览 |
| `comparison/comparison.json` | 各条件结果、同条件槽数对照，以及同槽数成本/粒度对照和一致性检查 |
| `comparison/<case>/` | 实际发送槽占用、依赖满足后等待、最长五例及来源摘要 |
| 各阶段 `commands/` 和 `probe.json` | 实际命令、退出状态、预算与资源消耗 |

这里的比较属于研究者核验，可以读取内部图和完成事件；其输出不能直接作为后续被动测量算法的可见输入。等待以 node·ns 或 rank·ns 分别计量，前者累计节点，后者按 rank 取并集，都不是整轮可节省时间。`round_reduction_fraction` 可以为负，负值表示该对照更慢；不要求实验获得正收益。

成本批次保留 `tempo_pairs`，粒度批次使用 `grain_pairs`；`slot_pairs` 在每档条件下比较单槽与双槽。粒度批次每组的 `segmentation` 保存逐任务核对后的 STORE/LOAD 任务数、有效分段总数及单分段任务数，不能把分段数与完整事务数混用。

准备阶段不运行仿真。`run-batch` 串行执行，仅复用同输入、同后端且证据完整的成功结果；失败不自动重跑。分析需重做时使用新的输出路径，通常不需要重复仿真。预算沿用共享累计 48 GiB，六组成本/槽数组合预留 4.5 GiB；单阶段 RSS 8 GiB、地址空间 12 GiB、1800 秒和至少 20 GiB 空闲不变。

小型程序检查：

```bash
"$sim_python" -m unittest discover -s experiments/scaleup_observability/tests -v
```
