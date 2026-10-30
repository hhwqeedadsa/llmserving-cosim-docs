# 独立任务共享网络时的完整备用代价

本实验检查目标任务启动成熟的延迟完整备用后，是否拖慢同一Clos中的另一个健康任务。先冻结输入和方法，再按同部署control/treatment比较；不以出现干扰为验收目标，不对结果调整请求数量或70微秒门槛。

[事前方法](protocol.md)、[结果网页](results/20261030/report.html)、[结论与边界](results/20261030/README.md)。多任务期限的通用接口见[公开示例](../../examples/scaleout_multicall/README.md)，与研究收益分开验收。

## 输入与执行

从仓库根目录运行。需要已构建、与当前固定版本匹配的RDMA服务后端、protoc和15份本地路由原件。没有自动下载、构建或安装。`prepare.py`核对原四份已知来源清单及新增接收清单的SHA，生成四份新结果目录的固定映射；原件变更、缺失或输出目录已存在均拒绝。

本次机器上的准备命令：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/multicall_externality/prepare.py \
  --known-manifest data/moe-trace-small/manifest.json \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --backend runs/delayed_whole_20261030/build/ns3.42-moe-rdma-service \
  --output runs/multicall_externality_20261030/study-counted
```

冻结记录里的`network_executed=false`表示准备时未执行网络，不是对后续运行状态的判断。实际四次执行由收据和各原生监控记录证明。

随后通过公开命令分别执行四个新条件。下面是control-normal；其他三次依照`frozen.json`的`cases`映射选择配置、协议与输出，不能覆盖已经成功的目录：

```bash
PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B -m moe_ub_cosim scaleout rdma-run \
  --config runs/multicall_externality_20261030/study-counted/input/service-control.json \
  --protocol runs/multicall_externality_20261030/study-counted/input/rdma-multicall-normal.json \
  --topology runs/multicall_externality_20261030/study-counted/input/topology \
  --backend runs/delayed_whole_20261030/build/ns3.42-moe-rdma-service \
  --protoc /home/lry/tools/protobuf/bin/protoc \
  --output runs/multicall_externality_20261030/study-counted/control-normal
```

| 条件 | service配置 | RDMA协议 |
|---|---|---|
| control-normal | service-control.json | rdma-multicall-normal.json |
| treatment-normal | service-multicall.json | rdma-multicall-normal.json |
| control-local | service-control.json | rdma-multicall-local.json |
| treatment-local | service-multicall.json | rdma-multicall-local.json |

正式执行前采用[事前方法](protocol.md)的累计CPU/磁盘与各项监控预算。本次额外使用本地host_probe包装各阶段，准确命令、终态及资源保存在结果收据；包装程序不构成实验驱动接口。

这些命令复用本机原件和构建产物。换机器时，先按实际路径取得并校验同一批原件、更新接收清单的本地路径、准备匹配后端与protoc，再用新的输出目录重新冻结。不能只把冻结文件中的绝对路径改掉并当作同一份历史证据。原始路由不随结果发布；新增11份未独立核验远端固定修订，不能凭本机SHA补造远端身份。

## 分析与网页

分析仅读取保存结果，不重跑网络，不将评价真值传回策略。输出目录必须不存在。

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/multicall_externality/analyze.py \
  --frozen runs/multicall_externality_20261030/study-counted/frozen.json \
  --output runs/multicall_externality_20261030/analysis-final

PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/multicall_externality/plot.py \
  --analysis runs/multicall_externality_20261030/analysis-final \
  --output runs/multicall_externality_20261030/figures

PYTHONPATH=python .venv/frontend/bin/python -B experiments/multicall_externality/report.py \
  --analysis runs/multicall_externality_20261030/analysis-final \
  --figures runs/multicall_externality_20261030/figures \
  --frozen runs/multicall_externality_20261030/study-counted/frozen.json \
  --output experiments/multicall_externality/results/20261030
```

独立分析从原件专家选择重建逐层Dispatch/Combine矩阵、声明计算与KV上下文；再核对实际图、阶段门限、源/接收字节、ACK、原生决策前缀和唯一替换定时器。健康包按stream/sequence/长度/重传序号/交换机出口配对，驻留来自同UID在同交换机的实际MacRx至PhyTxBegin。

队列积分先按顺序对齐完整包记录与原始队列样本：入队原始值是加入后占用，出队原始值是扣除前占用。使用实际包长恢复每次操作后占用，包含全部控制/数据协议、排除正在串行发送的字节，逐端口检查最终归零。`*-queue-integrals.csv`保存归一化后的端口积分和峰值；不是将原始出队前样本当成残留队列。

绘图需要本机现有Matplotlib；此次没有新增依赖。图片可独立导出PNG/SVG，网页本地打开不需要网络。分析CSV先归一为LF再散列，保证Git存储和交付一致。执行收据与交付核查为本次结果另行汇编，不作为运行策略输入。
