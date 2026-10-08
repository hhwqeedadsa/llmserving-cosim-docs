# 运行基础观测对照

本入口比较端口计数与计数加队列的候选窗口覆盖，规则、单位、评价边界见[观测方法](observation-method.md)。程序分为输入准备、联合仿真、观测准备、测量、评价；最后三项可以复用已完成的执行，无须再次仿真。

## 从新目录完整运行

在仓库根目录，使用已安装本项目的 Python 环境及按[用户指南](../../docs/user_guide.md)构建的联合后端。下面两个变量分别指向 Python 与后端，不依赖旧运行目录或真实数据账户。默认小场景全部输入由仓库的参考配置生成。

```bash
sim_python=.venv/llmservingsim-native/bin/python
sim_backend=build/v04-platform/output/cosim/ns3.44-cosim-service
study_root=runs/scaleup_observability/my-observation-study

"$sim_python" -m experiments.scaleup_observability prepare-observation-source --output "$study_root/source"
"$sim_python" -m experiments.scaleup_observability preflight --batch "$study_root/source" --output "$study_root/preflight.json"
"$sim_python" -m experiments.scaleup_observability run-batch --batch "$study_root/source" --backend "$sim_backend"
"$sim_python" -m experiments.scaleup_observability prepare-observations --sources "$study_root/source/sources.json" --output "$study_root/bundle"

"$sim_python" -m experiments.scaleup_observability measure-observations --observed "$study_root/bundle/observed/counts" --output "$study_root/counts"
"$sim_python" -m experiments.scaleup_observability evaluate-observations --truth "$study_root/bundle/truth" --predictions "$study_root/counts" --output "$study_root/evaluation-counts"
"$sim_python" -m experiments.scaleup_observability measure-observations --observed "$study_root/bundle/observed/counts-queues" --output "$study_root/counts-queues"
"$sim_python" -m experiments.scaleup_observability evaluate-observations --truth "$study_root/bundle/truth" --predictions "$study_root/counts-queues" --output "$study_root/evaluation-counts-queues"
```

`prepare-observation-source` 固定生成 seed=3、每 rank 8 请求、发送槽 1/2 的配对输入；不是任意规模扫描器。`batch.json` 保存输入摘要和实施代码摘要，`sources.json` 是后续观测准备的源清单。`run-batch` 只复用完整通过且摘要一致的运行；失败不自动重跑。

所有输出目录必须不存在。命令行相对路径均以仓库根目录为起点，源清单内部的 `batch` 路径以**源清单所在目录**为起点。各命令均有 `--help`。下面的小型检查无须启动后端：

```bash
"$sim_python" -m unittest discover -s experiments/scaleup_observability/tests -v
```

## 复用已经完成的输入对

另建 JSON 源清单，再执行 `prepare-observations` 及其后命令。下面是最小清单，`batch` 可以换成绝对路径：

```json
{
  "schema_version": "scaleup-observation-sources/1",
  "cases": [
    {"batch": "source", "case_id": "s3-b8-slots1", "role": "holdout"},
    {"batch": "source", "case_id": "s3-b8-slots2", "role": "holdout"}
  ]
}
```

每个输入集必须同时包含单槽和双槽执行，不允许重复。seed=0/1/2 标为 `development`（开发参照），seed=3 标为 `holdout`（规则固定后的复核）；最多 8 次执行。程序核验冻结输入、既有概览、实际发送提交/释放、配对任务图和平台版本。不接受混合后端、不完整的计数/队列覆盖、非同步时钟或超出 100 微秒的执行。改变研究区间或规则属于新的方法版本，不能修改结果文件来跳过检查。

## 输出怎样看

| 路径（相对于上例） | 内容及读取者 |
|---|---|
| `source/inputs/`、`source/runs/` | 固定输入、实际仿真与概览；布局同已有运行入口 |
| `bundle/observed/counts/` | 只有计数条件的 `manifest.json` 与 `windows.jsonl`；给计数测量程序 |
| `bundle/observed/counts-queues/` | 计数加队列的独立包；给队列条件测量程序 |
| `bundle/truth/` | 内部标签、源清单绑定、槽占用交叉核验；仅准备/评价可读 |
| `counts*/predictions.jsonl` | 完整窗口序列、是否选中、原因 `undetermined` |
| `counts*/summary.json` | 输入及方法摘要、处理量、实际输入/输出字节数与方法自身耗时 |
| `evaluation-*/evaluation.json` | 各输入/网格的 `cases` 及按角色合并的 `groups`，包含覆盖率、检查范围、误选和漏选 |
| 各分析输出下 `commands/` | 实际命令、工作目录、退出状态、日志和资源监控 |

公开包的 `manifest.json.topology` 保存静态节点、链路和 rank 对应节点；`nodes` 每行依次为节点号、类型、端口数、分配延时 ns、转发延时 ns，`links` 为源节点/端口、目的节点/端口、速率 bps、传播延时 ns，`rank_nodes` 为 rank/节点编号。两档同样开放这些静态信息。

公开包的 `manifest.json.fields` 规定 `windows.jsonl.ports` 中数值向量的顺序，端口顺序由 `port_ids` 规定。每行给出样本编号、网格、半开窗口和全部 32 个端口；没有按内部真值预选端口或时段。数值均为整数，时间为 ns，字节积分为 byte·ns。两档输入使用不同目录，不能把整个 bundle 交给测量入口。

评价字段 `wait_coverage_fraction` 是覆盖的满槽等待时间比例，`investigated_fraction` 是需要检查的声明 rank 时间比例；`investigated_active_fraction` 去掉执行结束后的空闲，仅用于评价。`wait_share_of_selected_time` 表示检查时间内实际目标占比，不是根因分类准确率。`false_lead_cells`、`missed_target_cells` 按 rank/窗口计数；`missed_wait_rank_ns` 按时间计算。原因未确定比例为 1，不能把未选中的窗口解释为没有就绪工作。

准备输出较详细，评价 JSON 较小；分享结果时可以只复制公开包或评价摘要，不必复制整个原始执行。公开包复制后仍以内容摘要绑定；测量程序不需要源路径。不要改名搬入 `truth/` 中的字段作为观测。

## 预算与失败

沿用共享预算：RSS 8 GiB、地址空间 12 GiB、每阶段 1800 秒、累计产物 48 GiB、文件系统至少 20 GiB 空闲。累计包含前期项目产物，目录列表在 [common.py](common.py) 的 `ARTIFACT_ROOTS`，新输出根也计入；新增大目录须登记，不能利用换目录绕过额度。两个仿真串行；分析入口同样通过平台 `host_probe` 监控。

资源监控和启动准备也消耗宿主时间，不能把命令总耗时当成仿真内部时间。若某阶段失败，命令返回非零并保留日志与已写产物；先查看失败原因，不自动提限、删除旧数据或复用部分产物冒充完成。调查修复后另行确定新的运行目录和重跑范围。
