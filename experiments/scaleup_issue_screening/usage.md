# 复现实验操作

先按[用户指南](../../docs/user_guide.md)准备 Python 环境和联合后端，所有命令从仓库根目录执行。下面使用本机已有环境；其他机器可替换 Python 和后端路径。实验模块通过 `PYTHONPATH=python:.` 加载。依赖仍按 gitlink 和 `deps.lock.json` 固定。

## 数据与输入

`configs/route-manifest.json` 固定每个源文件的仓库相对位置、SHA-256、数据集修订及未知来源信息。需要已经具备该修订的数据访问条件，并将文件置于清单指定位置；准备命令不联网、不下载、不使用模型权重。缓存缺失或摘要不符时停止，不用替代文件补齐。来源条款和获取说明见[CM384/R1 场景](../../docs/scenarios/cm384_r1.md)。

`configs/batch.json` 列出四个预定 case：`g0-b2-short`、`g0-b4-short`、`g0-b8-short`、`g0-b8-long`。它们的范围、计算成本和观察条件见[方法](method.md)。输入定义改变后必须使用新目录，不能覆盖旧结果。后端 SHA-256 必须与固定的验收二进制一致；其他机器重新构建产生不同摘要时，应先核验同一平台源码和依赖，再建立新的明确批次配置，不能静默忽略检查。

## 准备、运行与分析

以下以第一组为例，其他 case 只替换名称。准备阶段写独立配置，逐文件核验真实来源，调用 `moe-ub segment prepare` 与 `moe-ub resolve`。随后运行使用同一个公开 `moe-ub run`。

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.workflow prepare \
  --case g0-b2-short --output runs/scaleup_issue_screening/g0-b2-short \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.workflow run \
  --case-dir runs/scaleup_issue_screening/g0-b2-short
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.analyze \
  --case-dir runs/scaleup_issue_screening/g0-b2-short \
  --output runs/scaleup_issue_screening/analysis/g0-b2-short
```

分析先调用 `moe-ub validate`，再读取运行中封存的 `inputs/service.json`、`inputs/layer.json`、公开格式和内部身份，输出 `summary.json`、`ranks.jsonl`、`compute.jsonl` 和 `ports.jsonl`。分别是操作/网络摘要、各 rank 的进入与完成及工作量、后续实际计算节点时刻、覆盖端口统计。分析是研究者核验工具，会读 `truth/`，不能拿它作为只读公开观测的测量算法。没有将相关 rank 计为独立重复。

四个预定 case 全部分析完成后，可比较负载档位和短长段的相同分析层：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.compare \
  --analysis-root runs/scaleup_issue_screening/analysis \
  --output runs/scaleup_issue_screening/e1-comparison.json
```

比较会拒绝请求、路由、上下文、放置或参与者覆盖不一致的短长段。输出为描述统计，不自动赋予原因标签。分析目录在归档后保留，查看和比较时无需恢复全部大文件。

每个 case 中，`prepared/` 为带来源证据的实际层段输入，`resolved/` 为有效网络，`run/observed/` 为观察数据，`run/truth/` 和 `run/raw/` 为内部核验资料。`commands.jsonl` 保存公共命令、工作目录、退出码及日志名。资源报告位于相邻 `*-prepare/`、`*-resources/` 和 `run/logs/`，报告成功只代表其明确检查范围。

## 单因素对照输入

候选和对照对象确定后，可在同一 `workflow prepare` 命令中加 `--control CONTROL.json`。`--case` 仍指定正常基线，输出使用新目录。对照文件格式为：

```json
{
  "schema_version": "issue-screening-control/1",
  "case_id": "g0-b2-expert-half",
  "baseline_case": "g0-b2-short",
  "parameter": "routed_expert_per_input_ns",
  "reference": 100,
  "value": 50
}
```

这里只改变所选层段的单专家输入参考计算成本（单位 ns），同时更新模型成本和对应层成本。也支持 `parameter="copy_fence"`、`reference="data_ready"`、`value="local_read_complete"`，只改所选层的复制完成条件。每个文件只能声明一个因素，基线值不符则拒绝。来源、请求、容量、网络和其他计算成本保持原配置；改变计算或依赖后重新构图，不能修改已保存的图或时间戳。有效对照及配置摘要记录在 `case.json`。

正式选择与作用对象见[候选记录](candidates.md)。本批配置为 `configs/controls/expert-half.json`、`expert-double.json`、`local-fence.json`，均以 `g0-b2-short` 为基线。以第一项为例：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.workflow prepare \
  --case g0-b2-short --control experiments/scaleup_issue_screening/configs/controls/expert-half.json \
  --output runs/scaleup_issue_screening/g0-b2-expert-half \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service
```

准备后先核对输入只改变了声明因素，再沿用上节的 `workflow run`、`analyze` 和归档命令，只更换 case 目录；本批新增对照省略 `freeze`。

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.controls \
  --baseline-case runs/scaleup_issue_screening/restored-g0-b2-short \
  --case-dir runs/scaleup_issue_screening/g0-b2-expert-half \
  --output runs/scaleup_issue_screening/g0-b2-expert-half/input-audit.json
```

核验以正常运行封存的输入及实际网络文件为依据，会拒绝未声明的请求、成本、网络、观察配置和预算变化。仅允许网络元数据中的运行身份不同；种子、节点和链路等有效条件必须一致。分析时再次检查对照输入摘要与实际执行封存内容相符。使用新目录，逐项核验后再执行下一项，不同时运行多个后端。

恢复正常基线后，可用公开平台入口导出已选候选的内部事实：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim facts \
  --run runs/scaleup_issue_screening/restored-g0-b2-short/run \
  --config experiments/scaleup_issue_screening/configs/facts.json \
  --output runs/scaleup_issue_screening/facts/g0-b2-baseline
```

该策略限定第 7、8 层、128 个 rank 及明确数量和宿主预算。输出仅供研究者核验，位于 `truth/`，不能作为测量程序的默认输入。

如需复核保存的基线分析并追踪最晚前驱：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.analyze \
  --case-dir runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/analysis/g0-b2-baseline
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.inspect_facts \
  --facts runs/scaleup_issue_screening/facts/g0-b2-baseline \
  --analysis runs/scaleup_issue_screening/analysis/g0-b2-baseline \
  --output runs/scaleup_issue_screening/facts/g0-b2-witnesses.json
```

该检查器按固定规则选择各层最后完成和专家输入最多的端，逐次跟踪一条最晚前驱，保留并列项、未选范围边界和未知资源持有者。跨端追踪回到发送提交时刻，不能把较晚的 ACK 回收代入数据交付链。输出不是完整根因分类，也不是可节省时长的求和。


三个预定对照全部分析完成后，统一比较实际业务结束、操作时序、计算依赖和相同覆盖下的端口聚合：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.compare_controls \
  --analysis-root runs/scaleup_issue_screening/analysis \
  --output runs/scaleup_issue_screening/e2-comparison.json
```

比较会检查声明因素、输入核验摘要、实际请求/路由/上下文及参与者覆盖。结果是同一来源组内的模型效应，不把 rank 当作独立重复，也不将同种子写成包路径相同。

## 长段磁盘预估

短段完成后、归档前可记录其实际占用；归档完成后再估计长段与压缩包共存的峰值。估计命令只适用于方法中固定的三层与六层条件，不能当作任意输入的空间保证。

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.forecast \
  --measure-run runs/scaleup_issue_screening/g0-b8-short/run \
  --output runs/scaleup_issue_screening/short-resource-basis.json
# 按下节完成短段归档，再执行：
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.forecast \
  --basis runs/scaleup_issue_screening/short-resource-basis.json \
  --reference-archive runs/scaleup_issue_screening/archives/g0-b8-short.tar.gz \
  --output runs/scaleup_issue_screening/long-disk-forecast.json
```

本批六层长段省略 `freeze`，保留完整原始运行并直接归档。若以后需要切片，恢复完整目录后再用 `moe-ub segment freeze --from-run` 保存；重新检查当时预算。

## 保存与恢复

需要复用后端时，先通过平台入口保存有限层段；同文件系统的硬链接不会复制大图：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.workflow freeze \
  --case-dir runs/scaleup_issue_screening/g0-b2-short
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.archive pack \
  --input runs/scaleup_issue_screening/g0-b2-short \
  --output runs/scaleup_issue_screening/archives/g0-b2-short.tar.gz --release-expanded
```

归档保存整个 case，包括输入、原始结果及切片；按上述命令生成的分析另留在 `analysis/`。`--release-expanded` 只释放本实验拥有且已逐文件核验的展开文件；不加此参数则保留两份。压缩包旁的 `.json` 是摘要和文件清单，必须一起保存。归档失败会保留原因与原文件，不应直接删除未核验产物。

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.archive restore \
  --input runs/scaleup_issue_screening/archives/g0-b2-short.tar.gz \
  --output runs/scaleup_issue_screening/restored-g0-b2-short
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim slice inspect \
  --slice runs/scaleup_issue_screening/restored-g0-b2-short/slice --verify
```

恢复到新目录前检查原清单、压缩包摘要和预算。共享硬链接只计一次数据，并预留文件块取整和目录空间；超出预算则在展开前拒绝。恢复后的 `slice/` 可按平台指南使用 `sample` 和 `replay`，不需重新准备路由或构图。原 `run.json` 保留历史绝对路径用于审计；移动后重放使用可移动的切片，不直接执行旧路径配置。

本轮收尾已释放正式 case 的展开文件和临时恢复的基线副本。上文 `restored-g0-b2-short` 是执行恢复命令后生成的目录，不是交付时一直保留的目录。查看结论、运行统一比较只需保留的 `analysis/`；重新导出事实或核验完整输入时再恢复对应包。已保存的内部事实在 `facts/`，基线和替代完成条件两组使用同一配置。

## 不依赖真实数据的小检查

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_workflow
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_small \
  --output runs/scaleup_issue_screening/tooling-check-new \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service
```

`check_small` 也接受 `--control experiments/scaleup_issue_screening/configs/check/control-half.json` 或 `control-fence.json`，分别用独立合成输入验证成本与复制完成条件的修改路径。先选出层段，再解析该层段的有效场景；未执行的来源层不参与运行配置检查。

前者检查覆盖、三个时间指标、单因素输入隔离及归档拒绝条件；后者用本目录的合成小输入完成准备、运行、分析、保存、压缩、恢复和平台验证，不依赖真实来源或旧运行。合成结果只检查工具，不能作为正常业务发现。

资源上限由 `configs/host.json` 和公开运行配置明确给出，所有大操作均受监控。任何命令非零退出后先查看对应日志，不覆盖失败目录或自动加大上限。正常探索串行运行并归档，后续候选按方案门槛另行记录。

## 排队时长与实际路径补证

定义、精度与选择规则见[补证方法](delivery_method.md)。从完整原归档直接核算所有已声明出口的队列非空时长上下界，不展开任务图：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.duration_bounds \
  --archive runs/scaleup_issue_screening/archives/g0-b2-short.tar.gz \
  --output runs/scaleup_issue_screening/duration-new/g0-b2-short
```

输出 `ports.jsonl` 和 `summary.json`。上下界之差是不可恢复的不确定度；相邻正面积窗口不是连续积压的证明。其他三个 E1 case 使用同一命令替换名称。

实际路径需要新执行补记录。先完成不依赖真实数据及旧产物的小流程：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_delivery_math
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_delivery \
  --output runs/scaleup_issue_screening/capture-check-new \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service
```

`check_delivery` 生成合成输入，执行全出口聚合、保存与详细回放，核对不扰动、逐包路径和精确队列积分；其 `delivery-summary.json` 仅是工具检查。

正式 case 先按上文恢复到新目录，例如 `restored-g0-b2-short`，并确保原分析摘要可用。准备选取全静态最短路径出口并保存 `selection.json`，必要时用硬链接保存该 case 的切片；回放只执行后端：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.path_replay prepare \
  --case runs/scaleup_issue_screening/restored-g0-b2-short \
  --summary runs/scaleup_issue_screening/analysis/g0-b2-baseline/summary.json \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service \
  --fixed-pairs experiments/scaleup_issue_screening/configs/delivery-pairs.json \
  --output runs/scaleup_issue_screening/restored-g0-b2-short/detail
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.path_replay run \
  --case runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/restored-g0-b2-short/detail
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.path_replay compare \
  --case runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/restored-g0-b2-short/detail
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.path_analysis \
  --detail runs/scaleup_issue_screening/restored-g0-b2-short/detail \
  --output runs/scaleup_issue_screening/path-analysis-new/g0-b2-short
```

`compare` 先验证回放，再核对所有执行记录、原有观测和全部已声明出口的数据包内容/发送时刻摘要，生成 `equivalence.json`。`path_analysis` 输出逐包路径 `packets.jsonl`、任务比较 `transfers.json`、选定出口精确时长 `queues.json` 与核验摘要。它们属于研究者内部证据，会读取真值，不能直接供给 E3 测量算法。回放不等于新的独立请求样本。

对 `g0-b2-expert-half`、`g0-b2-expert-double`、`g0-b2-local-fence` 重复上述串行流程，替换 case 和对应分析摘要；保留相同 `delivery-pairs.json`。所有条件完成后比较共有任务：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.compare_paths \
  --baseline runs/scaleup_issue_screening/path-analysis-new/g0-b2-short \
  --controls runs/scaleup_issue_screening/path-analysis-new/g0-b2-expert-half \
    runs/scaleup_issue_screening/path-analysis-new/g0-b2-expert-double \
    runs/scaleup_issue_screening/path-analysis-new/g0-b2-local-fence \
  --output runs/scaleup_issue_screening/path-comparison-new.json
```

每个恢复 case 包含新 `detail/`，可用已有 `archive pack --release-expanded` 生成另一个完整归档后释放展开文件。路径分析另行保留，旧包不覆盖。串行恢复、补采、核验、归档，避免四份展开图同时占用磁盘；先检查现存累计占用与预计额外空间，不能用原执行峰值保证补采也能装下。

本轮补证根目录按 `durations/`、`paths/`、`archives/` 和 `path-comparison.json` 保存上述结果后，可统一核验四个正常 case 和四份补采的覆盖及归档绑定，不展开图或重跑仿真：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.review_delivery \
  --delivery runs/scaleup_issue_screening/delivery \
  --original-archives runs/scaleup_issue_screening/archives \
  --output runs/scaleup_issue_screening/delivery/acceptance.json
```

检查读取原包与新包摘要、指定清单成员以及统计文件，核对二进制、随机配置、实际完成时刻、不扰动证据和共有任务身份。逐文件完整归档核验由保存阶段完成；此命令不是硬件校准、E3 信息收益评价或新一次仿真。

核对 C1 两条既有时间链与实际对象参数时，直接流式读取原归档，CANN 源码目录使用来源清单指定的固定版本：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.mechanism_review \
  --archive runs/scaleup_issue_screening/archives/g0-b2-short.tar.gz \
  --witnesses experiments/scaleup_issue_screening/results/e2/baseline-witnesses.json \
  --source-root runs/v03-cann-behavior/cann-source \
  --source-manifest fixtures/m10_cann/source-evidence.json \
  --output runs/scaleup_issue_screening/c1-mechanism-review.json
```

输出保存分段时间、并列前驱、传输业务身份、实际对象回读和来源摘要。该操作不恢复大图或执行仿真；源码目录若移动，可修改 `--source-root`，文件内容仍须与清单一致。

## C1 原生请求队列核验

`native_review` 是本实验的研究者核验入口，会读内部真值。它复用原冻结图，选择 rank 70、65 从零时刻至 120000 ns 的完整请求队列，最多 100000 条记录、每条最多 4096 个队列或在途项。平台格式和独立合成示例见[请求队列诊断](../../docs/record_storage.md#原生请求队列诊断)与[小例](../../examples/native_first_round/README.md)。

先按上节恢复 `g0-b2-short` 到一个新目录。以下采用 `restored-g0-b2-short`，后端指向本次隔离构建；其他机器可替换路径。`--platform-commit` 必须与本机实际平台源码、锁文件和子模块一致，不绕过版本检查。原实验 `workflow` 的旧平台固定条件继续保留。

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.native_review prepare \
  --case runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/native-capture-new \
  --backend build/m19-a3-resources/c1-native/output/cosim/ns3.44-cosim-service \
  --platform-commit 4e23dda
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.native_review run \
  --case runs/scaleup_issue_screening/restored-g0-b2-short --output runs/scaleup_issue_screening/native-capture-new
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.native_review compare \
  --case runs/scaleup_issue_screening/restored-g0-b2-short --output runs/scaleup_issue_screening/native-capture-new
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.native_review analyze \
  --case runs/scaleup_issue_screening/restored-g0-b2-short --output runs/scaleup_issue_screening/native-capture-new
```

`compare` 核对原有全部执行记录、观测记录及端口包内容/发送时刻指纹；`analyze` 保存八条状态传输的直接阻塞分支、队首、跨核任务身份、请求字节范围及释放响应。完整过程在 `queue-review.json`，可读摘要为 `queue-summary.json`。时间区间没有重叠求和，但其中“额度满”和“排在其他任务后”是同一时段的两项事实，不能再相加。

使用 `archive pack --release-expanded` 保存并释放这份补采目录，再准备唯一的额度对照。沿用同一组 `prepare/run/analyze` 命令，把输出换成新目录 `native-quota64-new`，并在 **prepare** 中加入 `--store-outstanding 64`。配置依据为 `configs/controls/native-quota.json`：保持单 worker、4096 B 拆分与共享池 256，仅将 STORE 在途上限从 32 改成 64。行为改变后不运行等价 `compare`，改用：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.native_review effect \
  --case runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/native-quota64-new \
  --baseline-capture runs/scaleup_issue_screening/native-capture-new.tar.gz
```

`effect.json` 核对相同业务贡献、实际前驱和并列项、原消费者及实际最晚消费者、后续计算、包数、字节和端口分布。所选端点的实际请求字节范围逐项比较；其他端点受相同冻结计划与全局拆分参数约束。它不宣称 64 是硬件容量，也不将同一随机种子解释为相同包路径。

这些操作沿用 `configs/host.json` 的累计资源根目录和 48 GiB 硬上限。每次恢复、运行及压缩前按当前占用预检，串行执行；上面的目录名不表示可以同时保留全部展开副本。若只需原 run 和 slice，可经逐文件核验释放恢复目录中其他内容：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.archive release-restored \
  --input runs/scaleup_issue_screening/restored-g0-b2-short \
  --output runs/scaleup_issue_screening/archives/g0-b2-short.tar.gz \
  --keep-directory run --keep-directory slice
```

此处 `--output` 指已存在的来源归档，命令不会覆盖它。未登记文件、内容变化或归档校验失败均拒绝释放；外置 `.release.json` 记录释放范围。全部比较完成后，同一命令省略两个 `--keep-directory` 即可释放剩余恢复副本。随后将额度对照目录校验归档；原归档始终保留。
