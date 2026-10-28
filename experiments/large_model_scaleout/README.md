# 百B模型与300/500卡Scale-Out结构

本轮核实SimAI/Vidur和AICB固定版本的模型配置，准备精确300/500卡及标准320/512卡网络输入，并绘制拓扑和已执行的collective有效累计到达图片。拓扑生成与网络执行分别记录；前轮4卡真实路由结果不外推为本轮百卡结果。

精确300/500卡采用四卡服务器；320/512卡采用八卡服务器。R1有256路由专家，Qwen3-235B有128路由专家，EP须整除专家数和当前服务分组的GPU总数。GPU总数不等于单个All-to-All的参与rank数，DP副本组也不能当作一个全局collective。

交付见[离线网页](results/20261028-final/report.html)和[独立核验](results/20261028-verification.json)。六套网络结构各有PNG/PDF/SVG，另附Dispatch/Combine的PNG/PDF及四份曲线CSV。实际节点和全部物理链路均绘入SVG，独立解析核对共4,528条物理链路。网页中双plane重复展示同一批GPU，不能相加。

## 大模型结论

固定版本SimAI中有Qwen3-235B、Qwen3-235B FP8、DeepSeek-V3及R1-0528配置，Vidur文档列出推理和AICB/SimAI后端；仍保留“部分支持”“适配中”限制。固定AICB中有GPT175B训练预制workload，以及推理生成器和DeepSeek默认配置。上游12份来源文件和两份许可逐hash保存在[upstream](upstream/README.md)，原下载树和失败收据在`runs/large_model_scaleout_20261028/sources/`。未安装或执行SimAI/AICB。

可直接表达的Qwen3-235B形状已转换为`examples/scaleout_large_models/qwen235-{320,512}.json`：94层、128专家/Top8、GQA64个Q头/4个KV头、head_dim128，核算235,093,634,560参数。EP64每rank每层2专家；5/8组跨Pod推理副本，声明BF16权重约21.51GiB/rank、KV约0.370GiB/rank。源FP8配置的`model_type`等元数据与BF16不同，不直接覆盖BF16输入或虚构FP8性能。

当前ready-KV Decode输入：batch/rank=1、context=2048、decode_steps=2，合成路由和声明计算成本。batch/rank可在前端现有范围1—16修改；必须重新检查显存、工作量和公平性。80GiB内存声明未含激活/工作区/通信缓冲。完整R1仍缺当前Scale-Out前端的MLA、Dense与共享专家支持，当前真实R1路由也不是R1-0528来源。

300/500卡采用等大的EP组时最大EP4，Qwen BF16权重约120.65GiB/rank，容量检查明确拒绝，没有给这两套网络强行配整模型。可以另研究296/496工作卡加备用卡、非等大EP组、TP/PP或量化。标准320/512卡并非一次320/512-rank All-to-All；它们的全局并发也不同，不能直接称为固定请求负载的强扩展对照。

## 网络与曲线结果

| 结构 | GPU / 服务器 | ToR / Agg / Core | 物理链路 |
|---|---|---|---|
| Clos-300 | 300 / 75 | 25 / 20 / 8 | 440 |
| Clos-500 | 500 / 125 | 25 / 20 / 8 | 640 |
| Clos-320 | 320 / 40 | 20 / 20 / 8 | 440 |
| Clos-512 | 512 / 64 | 32 / 32 / 8 | 704 |
| HPN-inspired-320 | 320 / 40 | 64 / 16 / 8 | 960 |
| HPN-inspired-512 | 512 / 64 | 64 / 16 / 8 | 1344 |

Clos各层400G；HPN启发结构8 rail/2 plane、每plane接入200G、上层400G，共享400G/GPU预算；全边传播时延1µs。Agg/Core跨rail共享，不是生产HPN复刻或OCS。交换机上联存在汇聚比，网页逐层报告，不称无阻塞。同服务器GPU也走声明网络，不模拟NVLink。

生成器独立核对端点、服务器数、端口、重复边及每plane无GPU中转的交换网络连通性，导出ASTRA三字段和SimAI六字段拓扑格式；未用上游SimAI执行这些导出文件。六份公开`rdma-preflight`通过，四套320/512卡模型×Clos/HPN组合通过完整服务输入校验；没有新网络执行。

有效累计到达直接复用[前轮真实路由实验](../collective_arrivals/README.md)，图片和CSV逐字节核对。EP4四请求、原层3—4、两个Decode步骤，全任务76消息、2,609,152B，正常333.298µs、指定数据/ACK故障417.998µs。首Dispatch有效数据358,400B，收齐53.917→134.950µs；其累计有效到达排除重复和GBN丢弃的乱序包，区分源确认，不代表GPU内存可见。图中的4卡结果不能外推为百卡结果或真实GPU性能。

## 复现

从仓库根目录运行，输出目录必须不存在；需要既有Python及matplotlib，命令不安装依赖。建议沿用当前任务的host_probe监控：每命令60秒、RSS1GiB/地址2GiB，累计300受监控秒/128MiB产物，卷预留8GiB。本例仅静态生成/绘图，不启动服务。

```bash
PYTHONPATH=python python3 experiments/large_model_scaleout/generate.py \
  --repository "$PWD" --output /path/to/new-stage/topologies
PYTHONPATH=python python3 experiments/large_model_scaleout/prepare_models.py \
  --sources experiments/large_model_scaleout/upstream \
  --base examples/scaleout_arrivals/declared-119b.json \
  --output /path/to/new-stage/models
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONPATH=python \
python3 experiments/large_model_scaleout/render.py \
  --repository "$PWD" --stage /path/to/new-stage \
  --arrivals experiments/collective_arrivals/results/20261028 \
  --output /path/to/new-report
PYTHONPATH=python python3 experiments/large_model_scaleout/verify.py \
  --repository "$PWD" --stage /path/to/new-stage \
  --report /path/to/new-report \
  --original experiments/collective_arrivals/results/20261028 \
  --output /path/to/new-verification
```

模型转换只读取固定配置并核算形状/容量，不执行权重或算子；原119B文件仅作为当前schema与声明成本的模板，模型维度全部从Qwen上游覆盖。六套拓扑的参数入口及单独生成命令见[用户输入说明](../../examples/scaleout_large_models/README.md)。`verify.py`独立检查来源、完整服务输入、交换机初始缓冲、SVG物理路径数、离线HTML链接、原曲线复制与终态。证明范围是静态输入和既有4卡曲线，非百卡性能。

本轮首次绘图因Python排序key的lambda优先级错误在写图前失败，已修正并使用新输出目录，未抬高限额。失败日志和源码保留于`runs/large_model_scaleout_20261028/render-monitor/`与`render-failed-source.py`，没有重跑已完成网络。具体命令、退出状态和资源记录见运行目录；[收尾摘要](results/20261028-closeout.json)包含预算与交付核验结果。仅本地提交，不push。
