# 正常样本校准的完整备用对照

先从已有正常两步目标完成时间按1µs向上取整，固定新门槛；仅新增normal/local各一次，与不启动备用和旧70µs备用比较。此为成熟延迟备用的单样本基线标定，不是新算法、最优门槛或p99。方法见[执行前约定](protocol.md)，结果见[网页](results/20261030/report.html)及[结论](results/20261030/README.md)。

使用已有前端环境、匹配的原生后端和本地15份路由原件，不安装、下载或构建。仓库根目录准备新的输出：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/calibrated_hedge/prepare.py \
  --previous-study runs/sequential_whole_20261030/study \
  --output runs/calibrated_hedge_20261030/study
```

`frozen.json`记录门槛来源、原四份网络的全部文件SHA、新输入、后端与库身份。历史输出只读。按`new_cases`映射使用公开命令，以下为normal完整例，local只替换protocol和输出目录：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config runs/calibrated_hedge_20261030/study/input/service-multicall.json \
  --protocol runs/calibrated_hedge_20261030/study/input/rdma-multicall-normal.json \
  --topology runs/calibrated_hedge_20261030/study/input/topology \
  --backend runs/delayed_whole_20261030/build/ns3.42-moe-rdma-service \
  --protoc /home/lry/tools/protobuf/bin/protoc \
  --output runs/calibrated_hedge_20261030/study/calibrated-normal
```

每个新输出目录须不存在；原生预算与累计预算先落实。已有正常/故障结果不重跑；失败不自动重试或扩限。公开`scaleout rdma-check --run <目录>`只核验保存结果。

独立分析和报告也不执行网络，输出目录须新建：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/calibrated_hedge/analyze.py \
  --frozen runs/calibrated_hedge_20261030/study/frozen.json \
  --output runs/calibrated_hedge_20261030/analysis
PYTHONPATH=python .venv/frontend/bin/python -B experiments/calibrated_hedge/explain.py \
  --frozen runs/calibrated_hedge_20261030/study/frozen.json \
  --analysis runs/calibrated_hedge_20261030/analysis \
  --output runs/calibrated_hedge_20261030/analysis/mechanism.json
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/calibrated_hedge/report.py \
  --frozen runs/calibrated_hedge_20261030/study/frozen.json \
  --analysis runs/calibrated_hedge_20261030/analysis \
  --output runs/calibrated_hedge_20261030/report
```

`analyze.py`复用版本化的前轮独立读取器，并逐项核对原件矩阵、全部依赖/门限、源发送和接收字节、完整包/队列、原生定时/决策及真实KV释放；只读取器代码，不使用测试或旧结果驱动流量。`explain.py`将实际ACK包路径和门限余量关联，剩余运行时间只作事后评价，不输入策略。报告保留CSV和PNG/SVG，网页不依赖外部脚本。

换机器需要下载原件、可用前端、对应后端及protoc，再以新路径准备和冻结，不能手改历史冻结记录。原始路由与大型包记录不随报告发布。原件来源身份与完整推理/硬件校准边界沿用[前轮说明](../sequential_externality/README.md)。
