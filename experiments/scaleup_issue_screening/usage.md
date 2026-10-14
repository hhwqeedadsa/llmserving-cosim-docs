# 运行与复现入口

本页保存已验证的命令和阅读路径。命令从仓库根目录运行，先按[用户指南](../../docs/user_guide.md)准备 Python 环境与固定依赖。具体研究安排见[方案](plan.md)，状态和预算见[当前任务](../../docs/tasks/current.md)。

## 已有平台操作

原生 Linux 环境安装、构建和普通运行使用[平台用户指南](../../docs/user_guide.md)。有限连续层段及内部事实读取见[A3 层段示例](../../examples/a3_segment/README.md)。这些是已存在的平台入口，不代表本轮三类诊断实验已经实现或验收。

依赖仍使用父仓 gitlink 和 deps.lock.json，不自行更新上游。已有资料需要按运行清单核对版本、摘要和观察覆盖；程序接受显式路径，不把某台机器的 runs 目录当作必然存在。

## 历史实验复现

历史全部实际命令和归档使用方式保存在[封存版本的操作说明](https://github.com/baoy21/moe-ub-cosim/blob/b345694fbc2faed8feebb425d43c60f765f7eee9/experiments/scaleup_issue_screening/usage.md)。其中分别包括初始正常运行、成因对照、C1 请求与组织核验、N1/N2 网络分析以及最后的离线边界核验。各分支的结果与适用范围见[归档索引](archive/README.md)。

历史 Python 模块已迁入 `analysis/`，`check_*.py` 已迁入 `tests/`。需要完整重现历史运行时使用其固定代码和依赖，不把历史程序摘要替换成当前值绕过核验。封存说明中的机器路径是示例，按实际环境替换。

## 小型检查与模块帮助

不需要真实轨迹、模型或已有运行产物的分析检查：

```bash
PYTHONPATH=python:. python3 -m unittest \
  experiments.scaleup_issue_screening.tests.check_workflow \
  experiments.scaleup_issue_screening.tests.check_delivery_math \
  experiments.scaleup_issue_screening.tests.check_network \
  experiments.scaleup_issue_screening.tests.check_network_offline
```

其中 `PublicRecovery.test_fresh_synthetic_cli_flow_with_separate_evaluation` 从临时合成输入开始，调用简化关系准备、受限判断、公开计数恢复，再独立评价；测试还核对判断器实际打开的文件集合。它验证分析行为，不证明真实网络现象。

所有现有分析命令的模块名增加 `analysis` 层，例如：

```bash
PYTHONPATH=python:. python3 -m experiments.scaleup_issue_screening.analysis.workflow --help
PYTHONPATH=python:. python3 -m experiments.scaleup_issue_screening.analysis.network_bounds judge --help
PYTHONPATH=python:. python3 -m experiments.scaleup_issue_screening.analysis.network_recovery recover --help
```

`network_bounds judge` 显式接收 `--input`、`--semantics`、`--output`；可用 `--mode old|strong` 以及 `--relation` 比较历史规则与准备好的关联。`network_recovery recover` 接收 `--input`、`--semantics`、`--counters`、`--output`，不能传入内部真值。输出文件必须不存在，已有结果不会被覆盖。准备和独立评价入口分别见相应模块 `--help`。这些仍是历史结束边界问题的工具，不代表三类机制诊断已完成。

固定业务与网络输入在 `configs/baseline/`，正常批次定义在 `configs/scenarios/batch.json`，历史对照在 `configs/scenarios/controls/`；`configs/observation/host.json` 保存累计资源预算。历史配置内容和摘要保持不变，不因为迁移重新生成或重新绑定旧结果。

另有使用三请求、两层合成输入的公开准备—运行—分析—冻结—归档—恢复检查：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python \
  -m experiments.scaleup_issue_screening.tests.check_small \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service \
  --output runs/scaleup_issue_screening/my-small-check
```

输出目录必须是新目录；`summary.json` 保存检查结果，`case.tar.gz` 保存运行，`restored/` 保存经验证的恢复内容，旁边的 `*-resources/` 保存资源监控。此历史检查配置绑定 ns-3-UB `c0f1fe5`；示例二进制摘要为 `configs/scenarios/batch.json` 中的 `backend_sha256`。缺少该历史构建时，仍可运行前面的纯分析检查；不能传入不同依赖版本或修改版本检查绕过失败。新平台网络检查使用 `tests.check_network_replay --help` 及当前平台示例，与此历史配置分别绑定。

分析方法只读取明确开放的目录或文件；真值、干预安排和完整图由准备与评价读取。输入目录使用中性标识，不能从文件名获得答案。完整命令和执行日志留在 runs，相应摘要与使用入口在 Git 中维护。无实际验证前，不新增示范运行命令或宣称操作可用。

已验证的机制事实准备入口（在项目 Python 环境、仓库根目录运行）：

```bash
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_run prepare \
  --archive runs/scaleup_issue_screening/archives/g0-b8-short.tar.gz \
  --slice runs/scaleup_issue_screening/network-screening-20261013/discovery-slice \
  --backend build/m19-a3-resources/network-facts/output/cosim/ns3.44-cosim-service \
  --run-id r00 --output runs/scaleup_issue_screening/my-diagnostic-r00
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_run run \
  --output runs/scaleup_issue_screening/my-diagnostic-r00
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_run equivalent \
  --archive runs/scaleup_issue_screening/archives/g0-b8-short.tar.gz \
  --output runs/scaleup_issue_screening/my-diagnostic-r00
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_facts \
  --run runs/scaleup_issue_screening/my-diagnostic-r00/run \
  --output runs/scaleup_issue_screening/my-prepared-facts
```

第一个命令只准备和校验封存切片，不启动目标后端；`r00` 等条件来自 `configs/scenarios/diagnostic-study.json`，源档案、切片、二进制和准备程序摘要写入 `selection.json`。`run` 启动公开重放及校验；正常补采用 `equivalent` 对照原档案，受控运行不执行等价断言。事实准备要求已成功运行并保存 `network_facts` 的目录；输出 `input/` 是诊断白名单，`private/` 是评价和机制核查资料，不能混用。`summary.json` 绑定全部文件摘要和真实覆盖。目标执行开始前仍须登记当前任务的总次数与资源。

受限判断与独立评价分别执行，已用合成公开运行走通：

```bash
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_judge \
  --input runs/scaleup_issue_screening/my-prepared-facts/input \
  --output runs/scaleup_issue_screening/my-diagnosis
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_evaluate \
  --prepared runs/scaleup_issue_screening/my-prepared-facts \
  --predictions runs/scaleup_issue_screening/my-diagnosis \
  --output runs/scaleup_issue_screening/my-evaluation
```

判断器拒绝含额外文件的输入目录；不要将 `private/`、原始运行、控制配置或评价结果放进去。规则默认取 `configs/observation/diagnostic-rule.json`，也可显式 `--rule` 指定已经预登记的版本。判断与评价各自保存来源摘要；重分析使用新输出目录，缺少事件时指标保留未定义状态。

## 业务、逐包和历史复核

```bash
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_business \
  --case runs/scaleup_issue_screening/my-diagnostic-r00 \
  --output runs/scaleup_issue_screening/my-business
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_deep \
  --case runs/scaleup_issue_screening/my-diagnostic-r00 \
  --prepared runs/scaleup_issue_screening/my-prepared-facts \
  --business runs/scaleup_issue_screening/my-business \
  --output runs/scaleup_issue_screening/my-deep
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_historical \
  --normal-root runs/scaleup_issue_screening/my-normal \
  --output runs/scaleup_issue_screening/my-historical-review.json
```

`--case` 是有 `selection.json` 和成功 `run/` 的对照目录。受控业务复核必须增加 `--baseline <对应正常业务摘要.json>`，以检查图、计划传输、计算成本、资源与随机条件不变。逐包复核要求同源准备与业务摘要，只在研究者侧连接内部身份；八个详细出口来自预先固定六例。历史入口要求 `mechanism_screen` 已完成规则指定的正常档位，输出界限而非缺失字段的假造准确率。输出必须是新位置，自动受分析预算监督。

如最晚条件落在源端回调边界，可继续使用已有记录核验其前置提交：

```bash
PYTHONPATH=python:. python -m experiments.scaleup_issue_screening.analysis.diagnostic_feedback \
  --case runs/scaleup_issue_screening/my-diagnostic-r00 \
  --business runs/scaleup_issue_screening/my-business \
  --deep runs/scaleup_issue_screening/my-deep \
  --output runs/scaleup_issue_screening/my-feedback.json
```

该命令要求三个来源绑定一致，不运行后端。原生等待未拆分部分显式保留，不能将所有依赖边相加为可消除损失。

## 汇总诊断、代价和主题证据

八个固定条件的准备、判断、评价、业务、逐包及回调复核完成后，在仓库根目录运行：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python \
  -m experiments.scaleup_issue_screening.analysis.diagnostic_compare \
  --root runs/scaleup_issue_screening/diagnostic-study-20261014 \
  --output runs/scaleup_issue_screening/my-comparison.json \
  --summary-dir runs/scaleup_issue_screening/my-comparison-results
```

`--root` 下按固定条件 `r00`～`r07` 保存 `prepared-v3/<条件>/summary.json`、`judged-final/<条件>/`、`evaluated-final/<条件>/summary.json`、`business/<条件>/summary.json`、`deep-v3/<条件>/summary.json`、`feedback/<条件>.json`；另需同一规则的 `historical-final.json`。各公开模块通过 `--output` 放入对应位置。比较器验证摘要、正常补采等价及业务控制条件，拒绝混用规则或来源。

主 JSON 是研究者侧完整比较，包含私有依赖见证，只保存在本地。可选 `--summary-dir` 生成三个主题的 `study.json`、`measurement_budget/comparison.json` 和 152 行 `metrics.csv`，仅保留聚合指标与来源摘要，不导出包身份或真实路由条目。项目结果目录保存的是这组聚合文件的逐字节副本。输出文件和摘要目录均须不存在；命令受分析预算监督，旁边的 `*-resources/` 保存命令、退出状态和资源记录。

读取已有比较不需要重新运行后端。当前主记录为 `runs/scaleup_issue_screening/diagnostic-study-20261014/final-comparison.json`；主题汇总的研究入口见[本实验索引](README.md)。规则和事实准备的旧版本保留在各自本地目录，不作为最终比较输入。

## 访问已归档的运行

本轮 `r00`～`r03` 的展开副本已在逐文件校验后无损归档；比较器可直接校验其 `archives/target-rNN.tar.gz` 及索引，不要求同时恢复。查看原始运行时可以按需要恢复一个，目标位置必须不存在：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python \
  -m experiments.scaleup_issue_screening.analysis.archive restore \
  --input runs/scaleup_issue_screening/diagnostic-study-20261014/archives/target-r00.tar.gz \
  --output runs/scaleup_issue_screening/diagnostic-study-20261014/target/r00
```

恢复会核对文件摘要、硬链接及累计空间，超过当前预算即失败，不自动扩大额度。不要一次性解压所有历史数据，也不要把恢复归档等同于再次执行仿真。原命令、平台和来源绑定保留在归档内；需要重分析时使用新的分析输出位置。
