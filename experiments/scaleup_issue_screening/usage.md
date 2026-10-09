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

分析先调用 `moe-ub validate`，再读取公开格式和内部身份，输出 `summary.json`、`ranks.jsonl`、`compute.jsonl` 和 `ports.jsonl`。分别是操作/网络摘要、各 rank 的进入与完成及工作量、后续实际计算节点时刻、覆盖端口统计。分析是研究者核验工具，会读 `truth/`，不能拿它作为只读公开观测的测量算法。没有将相关 rank 计为独立重复。

四个预定 case 全部分析完成后，可比较负载档位和短长段的相同分析层：

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.compare \
  --analysis-root runs/scaleup_issue_screening/analysis \
  --output runs/scaleup_issue_screening/e1-comparison.json
```

比较会拒绝请求、路由、上下文、放置或参与者覆盖不一致的短长段。输出为描述统计，不自动赋予原因标签。分析目录在归档后保留，查看和比较时无需恢复全部大文件。

每个 case 中，`prepared/` 为带来源证据的实际层段输入，`resolved/` 为有效网络，`run/observed/` 为观察数据，`run/truth/` 和 `run/raw/` 为内部核验资料。`commands.jsonl` 保存公共命令、工作目录、退出码及日志名。资源报告位于相邻 `*-prepare/`、`*-resources/` 和 `run/logs/`，报告成功只代表其明确检查范围。

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

恢复到新目录前检查原清单、压缩包摘要和预算。恢复后的 `slice/` 可按平台指南使用 `sample` 和 `replay`，不需重新准备路由或构图。原 `run.json` 保留历史绝对路径用于审计；移动后重放使用可移动的切片，不直接执行旧路径配置。

## 不依赖真实数据的小检查

```bash
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_workflow
PYTHONPATH=python:. .venv/llmservingsim-native/bin/python -m experiments.scaleup_issue_screening.check_small \
  --output runs/scaleup_issue_screening/tooling-check-new \
  --backend build/m19-a3-resources/output/cosim/ns3.44-cosim-service
```

前者检查覆盖、三个时间指标及归档拒绝条件；后者用本目录的合成小输入完成准备、运行、分析、保存、压缩、恢复和平台验证，不依赖真实来源或旧运行。合成结果只检查工具，不能作为正常业务发现。

资源上限由 `configs/host.json` 和公开运行配置明确给出，所有大操作均受监控。任何命令非零退出后先查看对应日志，不覆盖失败目录或自动加大上限。正常探索串行运行并归档，后续候选按方案门槛另行记录。
