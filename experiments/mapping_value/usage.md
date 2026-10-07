# 运行映射价值实验

本实验使用已安装的 `moe-ub-cosim` 和已构建的联合后端。安装方法见[平台指南](../../docs/user_guide.md)。下列命令从仓库根目录执行，使用安装了平台及 `frontend` 依赖的 Python；命令行路径相对当前目录，所有输出目录或结果文件必须尚不存在。

## 小场景正常组与干预

```bash
python -m experiments.mapping_value run-small \
  --backend build/default/output/cosim/ns3.44-cosim-service \
  --output runs/mapping_value/demo
python -m experiments.mapping_value freeze-small \
  --baseline runs/mapping_value/demo \
  --output runs/mapping_value/demo/frozen
python -m experiments.mapping_value run-case \
  --baseline runs/mapping_value/demo \
  --case runs/mapping_value/demo/frozen/r001.json \
  --backend build/default/output/cosim/ns3.44-cosim-service \
  --output runs/mapping_value/demo/r001
```

`run-small` 从版本化的[小场景配置](configs/small/README.md)复制输入，依次调用平台 `resolve`、`run`、`validate` 和 `slice create`。正常运行在 `r000/`，完整首轮数据包在 `slice/`，实际命令和退出状态在 `commands/`。数据包与源运行使用不可修改的硬链接，共享磁盘字节；修改输入应另建配置与运行。

`freeze-small` 只读取正常参考：以第 2 个 MoE 层全组 Dispatch/Combine 操作跨度为尺度，为 rank 3 增加跨度的 10% 或 50% 的准备工作，再按每 rank 4 个 Token 向上取整到 ns/Token。出口 8:4 的两个服务档位是 112/56 Gb/s，时段对齐到正常目标层的 100 ns 窗口边界。`r005` 的服务区间位于正常整轮完成后，作为空闲对照。冻结文件保存具体数值和正常清单摘要，不依据异常运行结果再增加强度。

`run-case` 只执行指定文件。`r001`、`r002` 改变端侧图成本，重新构图运行；`r003`–`r005` 用公开 `replay` 重放相同图，只覆盖物理出口服务区间。运行结果在该次输出的 `run/`，内部干预声明在 `case.json`；这些目录不能直接交给分析程序。失败不自动重跑或扩大预算，应保留错误后另建经修正的运行。

## 准备、分析与评价

```bash
python -m experiments.mapping_value prepare \
  --run runs/mapping_value/demo/r001/run \
  --reference runs/mapping_value/demo/r000 \
  --config experiments/mapping_value/configs/small/experiment.json \
  --output runs/mapping_value/demo/prepared-r001
python -m experiments.mapping_value validate-input \
  --input runs/mapping_value/demo/prepared-r001/E3
python -m experiments.mapping_value analyze \
  --input runs/mapping_value/demo/prepared-r001/E3 \
  --output runs/mapping_value/demo/analysis-r001-E3.json
python -m experiments.mapping_value evaluate \
  --run runs/mapping_value/demo/r001/run \
  --reference runs/mapping_value/demo/r000 \
  --analysis runs/mapping_value/demo/analysis-r001-E3.json \
  --prepared runs/mapping_value/demo/prepared-r001 \
  --case runs/mapping_value/demo/frozen/r001.json \
  --config experiments/mapping_value/configs/small/experiment.json \
  --output runs/mapping_value/demo/evaluation-r001-E3.json
```

准备目录中的 `E1`–`E5` 是可以分别交给分析程序的封闭输入。`E3S`、`E5S` 另外纳入真实 `dispatch_count`、`combine_flag`、`load_ready` 应用状态关系；它们不改变 E3/E5 原有业务关系的定义。各条件均含同口径正常参考和端侧阶段。队列条件仅开放预先选定的六个出口，未采集的出口不能补零。

`projection/` 是准备程序的共享文件池，`preparation.json` 保存运行绑定、实现摘要和来源；二者都不交给分析。条件目录中的文件可以单独复制到没有原始运行数据的目录，分析只接受这一份目录。目录白名单、字段、摘要、引用、窗口范围与重复项在分析前检查，拒绝符号链接、全局操作号、关系字节权重及额外队列文件。

当前分析规则及其推断边界见[方法说明](method.md)。评价输出保存实际整轮差值、局部持续时间变化、完成时刻后移和候选范围；不能把多个后继的后移量相加当作整轮损失。已有研究结果见[结果记录](results.md)，执行进度只看[当前任务](../../docs/tasks/current.md)。

## 资源和检查

小运行通过平台预算限制 RSS 8 GiB、单进程地址空间 12 GiB、单阶段 1800 秒；正常执行上限为 1 ms 仿真时间。记录与导出各有 512 MiB 压缩预算；分析输入每张表最多 200 万行、512 MiB，单条最多 64 KiB。准备时相同文件在各信息条件间用硬链接复用。

累计产物限 32 GiB，并至少保留 20 GiB 文件系统空闲；代码中的 `ARTIFACT_ROOTS` 同时统计新实验根与此前登记的 M10/M16 根。`run.json` / `execute.json` 保存本次实际绝对路径和预算。使用其他运行位置时，平台仍计入本次输出，不能借此避开已有根。不要通过修改预算字段绕过一次失败。

研究输入隔离与人工差值检查无需后端、真实数据或历史产物：

```bash
python -m unittest discover -s experiments/mapping_value/tests -v
python -m experiments.mapping_value --help
```
