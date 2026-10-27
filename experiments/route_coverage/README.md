# 真实路由覆盖与扩充

本目录盘点当前可读取的真实请求文件，准备少量、分来源的后续数据，并核算其声明部署下的通信需求。它不运行网络，不生成实测GPU时间。边界与获取预算见[事前协议](protocol.md)，实际结果见[报告](results/20261026/report.md)。

2026-10-27用户又提供13份R1/MMLU文件，11份结构完整、2份缺层；加上原四份，当前离线可分析15份。新增远端固定修订内容身份仍待核验，接收结果见[新增数据报告](results/local_additions_20261027/report.md)，不能沿用旧报告的“本机只有四份”作为当前事实。

## 用户新增文件的接收

该操作只写本地接收报告与离线需求，不生成来源身份尚未核实的可执行平台manifest。目录来源须由用户明确确认；当前已确认全部为DeepSeek-R1-AWQ/MMLU。完整规则见[接收协议](local_additions_protocol.md)。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/route_coverage/local_additions.py \
  --directory data/moe-trace-small \
  --existing-manifest data/moe-trace-small/manifest.json \
  --catalog-cache runs/moe_trace_dataset_review_20261018/dataset-api.json \
  --output-dir runs/my-local-additions

PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/route_coverage/check_local_additions.py \
  --directory data/moe-trace-small \
  --existing-manifest data/moe-trace-small/manifest.json \
  --catalog-cache runs/moe_trace_dataset_review_20261018/dataset-api.json \
  --analysis-dir runs/my-local-additions \
  --output runs/my-local-additions-check.json
```

单文件8MiB、总源64MiB、最多64文件；扫描只接收数字名JSON，完整文件保留全部入口，缺层文件整份排除且记录位置。当前独立checker针对这17个原件的已知完整/缺层结果，不把固定期望当通用数据质量证明。`intake.json`记录来源、结构、SHA256及拒绝原因；`traffic-demand.json`记录合格文件、batch分组、共同观察长度及未用请求/步骤，不会修改旧manifest。

## 现有原件的操作

从仓库根目录执行，输出目录必须不存在。原件由用户先前下载，位于 `data/moe-trace-small/`，不随Git分发。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/route_coverage/analyze.py \
  --manifest data/moe-trace-small/manifest.json \
  --output-dir runs/my-route-coverage

PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/route_coverage/check.py \
  --manifest data/moe-trace-small/manifest.json \
  --analysis-dir runs/my-route-coverage \
  --output runs/my-route-coverage-check.json
```

输入沿用公开 `moe-ub-route-manifest/1`。`--max-files` 默认64、最多64；`--max-input-bytes` 默认及上限128MiB，单文件最多8MiB。文件逐份读取、校验SHA256及全部入口/层/TopK；拒绝重复内容、坏文件和不足的输入预算。多个模型分别使用各自清单，不混用层、专家数或TopK。

`coverage.json` 输出逐请求长度、实际来源、Prefill与Decode计数、每层热点摘要；`traffic-demand.json` 输出各batch的声明通信需求。后者采用四个目的rank、每层专家连续等容量放置、请求序号模4指定源rank、H×2字节参考激活。每token对同一远端rank只发一份向量；不包含控制/元数据/包头/ACK/Combine。batch指一个EP组内的独立请求数，增加batch同时改变活动源数量，不能作为固定源数量下的每卡batch因果实验。

每个batch档内按清单顺序组成互不重叠的请求组，各组用所有成员共有的Decode前缀；不足的请求与多出的步骤计入unused字段，不复制、不补齐。超过当前独立请求数的batch明确输出不可用。统计中的“波次”是一组请求的一层、一个步骤，不是新的独立请求；峰均比包含四个目的rank的零流量项，纯本地波次不计算该比值。

## 准备更多原始文件

已保存[64条固定版本下载链接](results/20261026/download-urls.txt)和[分组/请求级划分清单](results/20261026/download-plan.json)。每组16条，英文MMLU两个学科、中文MMLU一个学科和代码执行任务，共四组。选择不读取路由热度，排除原先四个原路径。开发与留出候选各32条；在检查与历史请求/同源题目的重合之前，不把候选称作严格留出集。正文表格是现有四份的结果，不是这64份的结果。

本轮四个公开目录请求和一个原始文件请求均连接失败，新增文件为零。新目录未读通，清单回退到已固定SHA256的历史可见路径列表；其中恰好100000条siblings，不能证明完整性，候选缺少大小和远端内容摘要，不能自动获取或独立证明固定版本身份。清单的预算128MiB是获取上限，不是已经核实的总大小。

如果从浏览器下载，建议放在：

```text
data/moe-trace-expanded/27febb7b2d24169560a9f6f83d38eb53f94916cf/
  cognitivecomputations/DeepSeek-R1-AWQ/
    mmlu/computer_security/1525.json
    ...（保留download-plan中的完整相对路径）
```

路径可防止不同任务的同名JSON互相覆盖。下载后应先取得完整固定版本目录或远端摘要，核验身份/全文件格式/重复请求，再组装新的manifest并运行上述命令。保存本机SHA256能固定当前内容，但不能单独证明内容来自某个远端修订。不能直接把未下载候选改写成已经可用的来源清单。

本轮目录探测入口如下。它会联网，执行前仍须遵守协议预算，不作为上述离线分析的必需步骤。没有自动读取令牌、接受条款或重试。历史元数据缓存位于忽略的 `runs/moe_trace_dataset_review_20261018/`，完整SHA见代码与来源报告；没有该缓存的检出无法使用回退计划，已有下载链接仍可查看。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/route_coverage/catalog.py \
  --output-dir runs/my-route-catalog \
  --cached-api runs/moe_trace_dataset_review_20261018/dataset-api.json \
  --existing-manifest data/moe-trace-small/manifest.json --probe-raw
```

更多数据的顺序是：先取得并校验这64份，再视首批有效文件和体积确定256/1024请求池。后两档尚未下载、不在本轮获取预算内。扩充首先验证热点跨任务/请求是否稳定，再用独立请求组成不同batch，在相同网络配置下检验网络结论。真实文件数量、活动rank数、EP组大小和完整千卡联合执行是不同指标，不能互相替代。
