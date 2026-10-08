# 首批正常实验的运行与查看

在仓库根目录使用已安装平台及 frontend 依赖的 Python，后端采用现有构建；安装方法见[平台用户指南](../../docs/user_guide.md)。本实验不依赖旧运行、其他实验驱动或测试 fixture。输入参数与确定性规则见[配置说明](configs/README.md)，统计定义见[概览方法](overview-method.md)。

```bash
python -m experiments.scaleup_observability prepare \
  --output runs/scaleup_observability/normal-demo
python -m experiments.scaleup_observability preflight \
  --batch runs/scaleup_observability/normal-demo \
  --output runs/scaleup_observability/normal-demo/preflight.json
python -m experiments.scaleup_observability run \
  --batch runs/scaleup_observability/normal-demo --case s0-b2 \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service
```

`prepare` 一次冻结九组输入及摘要，不执行仿真。`s0-b2` 表示第 0 组输入、每 rank 2 请求。`run` 依次公开解析配置及容量、执行、导出、校验、生成概览；计算图由本次请求和路由重新生成。每项产物保存实际命令、工作目录、退出状态、版本和输入摘要。

完成一个小型流程后，可串行完成整个首批并汇总：

```bash
python -m experiments.scaleup_observability run-batch \
  --batch runs/scaleup_observability/normal-demo \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service
python -m experiments.scaleup_observability report \
  --batch runs/scaleup_observability/normal-demo \
  --output runs/scaleup_observability/normal-demo/report.json
```

环境已有 Matplotlib 时，可生成独立 SVG、PNG 概览图及绘图数据；这是可选实验依赖，未增加平台依赖：

```bash
python -m experiments.scaleup_observability plot \
  --batch runs/scaleup_observability/normal-demo \
  --output runs/scaleup_observability/normal-demo/figures
```

图中比较整轮时间、单轮完成速率、最忙交换出口的实际发送占用率，以及同大小跨服务器 LOAD 任务的第 95 百分位时延。颜色表示输入组，同一组不同规模是嵌套请求集合；不由这九点推断统计显著性。

`run-batch` 只复用已有 `complete.json` 且输入、后端、运行与概览摘要均通过核验的成功组。已有但未完成的目录会报错，不自动重新运行、删除或放宽预算。保留失败原因，修正后另建明确的新目录。所有新输出目录或汇总文件须不存在。

```text
normal-demo/
  batch.json                 九组输入选择、文件摘要及生成实现
  inputs/s0-b2/              本组独立服务、模型、网络和观测配置
  runs/s0-b2/
    preflight.json           累计预算与剩余组预留
    commands/               resolve、run、validate 的命令与日志
    resolved/               公开解析输出及容量核对资料
    run/                    平台 observed、truth、raw、metrics、logs
    overview/               summary.json、overview.md、三层 CSV、进度 CSV
    complete.json           成功证据及摘要绑定
  report.json                九组一致性核对及结果汇总
```

单独重新生成研究者概览，不重跑网络：

```bash
python -m experiments.scaleup_observability overview \
  --run runs/scaleup_observability/normal-demo/runs/s0-b2/run \
  --output runs/scaleup_observability/normal-demo/overview-review-s0-b2
```

概览可以读取内部执行，不能当作下一阶段测量方法的封闭输入。原始运行保持不变；重新概览使用新目录。仅保存聚合记录、不含详细队列及物理发送区间的运行不能交给此首批概览程序。

小场景执行、导出、解析、校验及概览各阶段上限为 RSS 8 GiB、地址空间 12 GiB、1800 秒。累计产物上限 48 GiB，含当前任务登记的旧根、新实验根及本次输出，至少保留 20 GiB 空闲。预检按每个未完成组 768 MiB 估算新增预留；它是运行准备额度，不替代执行中的累计监测。超限保留错误，不自行提限或清理旧资料。

```bash
python -m unittest discover -s experiments/scaleup_observability/tests -v
python -m experiments.scaleup_observability --help
```
