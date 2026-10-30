# 源确认超时与完整备用的在线对照

比较同一早时刻直接启动完整备用，和按实际发送后的源确认超时才启动。固定128端点Clos及15份真实专家路由的两步片段，成熟SR/DCQCN保持。平台验收见[说明](../../fixtures/ack_guard_whole/README.md)，研究规则见[执行前方法](protocol.md)。

本轮[网页](results/20261030/report.html)和[结论](results/20261030/README.md)同时报告目标收益、健康影响、字节/计算及收尾，区分早启动与确认信号的作用。

以下命令在仓库根目录、已有前端环境/后端/protoc及原始路由齐备后使用。每轮使用新目录；失败不自动扩限或重跑，预算须在执行前落实。历史输出只读。

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/ack_guard_whole/prepare.py \
  --previous-study runs/sequential_whole_20261030/study \
  --output runs/ack_guard_whole_20261030/study

PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/ack_guard_whole/run.py \
  --frozen runs/ack_guard_whole_20261030/study/frozen.json \
  --case fixed-normal --protoc /home/lry/tools/protobuf/bin/protoc
```

其余三个case为`fixed-local`、`guarded-normal`、`guarded-local`，各执行一次。run.py校验固定输入/后端/库身份，随后调用公开`scaleout rdma-run`，保存命令与退出状态，不导入平台内部策略或测试驱动。原生预算由冻结配置实施，外层仍须监控累计时间/空间。

完成后只读分析：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/ack_guard_whole/analyze.py \
  --frozen runs/ack_guard_whole_20261030/study/frozen.json \
  --output runs/ack_guard_whole_20261030/analysis

PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/ack_guard_whole/explain.py \
  --frozen runs/ack_guard_whole_20261030/study/frozen.json \
  --analysis runs/ack_guard_whole_20261030/analysis \
  --output runs/ack_guard_whole_20261030/analysis/mechanism.json

PYTHONPATH=python .venv/frontend/bin/python -B experiments/ack_guard_whole/check_prefix.py \
  --frozen runs/ack_guard_whole_20261030/study/frozen.json \
  --output runs/ack_guard_whole_20261030/analysis/prefix-checks.json

PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/ack_guard_whole/report.py \
  --frozen runs/ack_guard_whole_20261030/study/frozen.json \
  --analysis runs/ack_guard_whole_20261030/analysis \
  --output runs/ack_guard_whole_20261030/report
```

`inspection.py`从前轮固定版本独立读取器复制并扩展新格式，文件头保留原SHA；原读取器及旧冻结记录保持。`guard_prefix.py`仅用实际交错OBSERVE/DECIDE过去前缀，独立重建检查、下一时刻、原生定时器、提交和两步退休。其余原件矩阵、计算、图依赖、阶段门限、实际包/队列、源确认与KV释放全部复核。算法读取与评价读取分开，不把未来ACK输入在线策略。

真实的是专家选择，不是实机数据包；BF16布局、计算与现成等价副本/KV为声明。只含两层、两个Decode步、单正常样本及已知局部故障，不能外推完整671B、持续推理服务、p99、硬件吞吐或经济收益。
