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
