# 运行发送槽对照

本入口使用现有[正常实验命令](usage.md)执行三个双槽场景，并将其与同输入的三个单槽基线配对。方法、固定参数和解释边界见[对照方法](slots-method.md)。在仓库根目录使用已安装平台依赖的 Python；不需要新构建或额外绘图库。

如果已有首批成功基线，直接将下面的 `normal-demo` 换成对应批次目录。如果从新目录开始，可独立生成输入，只运行所需的三个单槽基线：

```bash
python -m experiments.scaleup_observability prepare \
  --output runs/scaleup_observability/normal-demo
for case in s0-b8 s1-b8 s2-b8; do
  python -m experiments.scaleup_observability run \
    --batch runs/scaleup_observability/normal-demo --case "$case" \
    --backend build/v04-platform/output/cosim/ns3.44-cosim-service || break
done
```

`prepare` 冻结全部九组输入；此处只需三个每 rank 8 请求组成功，不要求运行其余六组。正常批次的磁盘预检仍按所有未完成组保守预留。失败时保留日志，不能将中断的基线当作成功配对。

```bash
python -m experiments.scaleup_observability prepare-slots \
  --baseline runs/scaleup_observability/normal-demo \
  --output runs/scaleup_observability/slots-demo
python -m experiments.scaleup_observability preflight \
  --batch runs/scaleup_observability/slots-demo \
  --output runs/scaleup_observability/slots-demo/preflight.json
python -m experiments.scaleup_observability run-batch \
  --batch runs/scaleup_observability/slots-demo \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service
python -m experiments.scaleup_observability compare-slots \
  --batch runs/scaleup_observability/slots-demo \
  --output runs/scaleup_observability/slots-demo/comparison
```

`prepare-slots` 先验证固定输入规则和三个单槽完成摘要，再冻结仅改变发送槽的新输入，不运行仿真。新批次包含 `s0-b8-slots2`、`s1-b8-slots2`、`s2-b8-slots2`；基线以目录和摘要引用，保留原始文件。运行前再核对成对输入和后端。若需搬移基线，按新位置重新生成一个对照批次，不手工改写已冻结的清单。

`run-batch` 串行调用公开解析、执行、导出、校验和概览流程。只复用摘要匹配的成功组，不重跑已有失败目录。`compare-slots` 核对同一平台、后端、统计口径、工作量及依赖图，随后分析六次执行。它需要完整内部资料，输出供研究者核验，不作为后续被动测量算法的默认输入。

```text
slots-demo/
  batch.json                       固定三对选择、新输入摘要和基线引用
  inputs/s0-b8-slots2/              新场景配置；另外两组同级
  runs/s0-b8-slots2/                与正常实验相同的运行、概览和命令结构
  comparison/
    comparison.json                三对变化、工作量核对和证据摘要
    commands/                      有界分析命令与资源日志
    s0/
      matched-nodes.csv            同一节点跨运行的开始、完成和等待差值
      slots1/slot-occupancy.csv     每 rank 的发送槽占用区间
      slots1/send-waits.csv         全部 SEND 开始前等待及槽满/空槽分解
      slots1/longest-waits.json     最长五段等待、前驱和占用/释放事件
      slots1/summary.json          本次执行的汇总及文件摘要
      slots2/                     对照执行的同口径资料
    s1/                            其余输入组同结构
    s2/
```

所有输出目录须不存在。双槽批次按三个未完成组各 768 MiB 预留；每阶段仍限制 RSS 8 GiB、地址空间 12 GiB、1800 秒，累计产物含旧目录不超过 48 GiB，至少保留 20 GiB 空闲。新输入或输出不会获得独立的磁盘额度。

检查输入约束和手工统计用例：

```bash
python -m unittest discover -s experiments/scaleup_observability/tests -v
python -m experiments.scaleup_observability prepare-slots --help
python -m experiments.scaleup_observability compare-slots --help
```
