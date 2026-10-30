# 128卡Clos局部故障对照

从用户允许的128/256/512中只选128，本目录研究同一Clos上两个独立EP64组的故障影响。输入是15份不同的真实R1-AWQ/MMLU专家选择；前端实际使用LLMServingSim，任务依赖由ASTRA调度，包、拥塞控制及恢复在同一原生RDMA ns-3时钟推进。方法及模型边界见[事前方法](protocol.md)。

`normal`不注入故障；`local`只在GPU20的前100us丢弃目的数据。故障位置来自运行前的第一层Dispatch最大入量规则，不由完成时间挑选。两者都用选择性重传与参考DCQCN，工作量、物理网络、持久连接和阶段完成模式相同。其他输入重新准备时，热点rank须由同一规则重新计算，不能固定假设为20。

## 程序分工

| 程序 | 输入与产出 |
|---|---|
| `prepare.py` | 原件目录、已有来源intake → 来源快照、拓扑、共享服务配置、两份协议和独立核算的计划图；不运行网络 |
| `run.py` | 冻结准备目录、原生程序身份收据、protoc → 指定一个条件；检查版本/输入、拒绝重复执行，并调用公开`rdma-run/rdma-check/data-loss-check` |
| `analyze.py` | 两个真实执行原件 → 原始JSON矩阵核算、消息与端点CSV、有效到达曲线、完成时间与网页 |
| `mechanism.py` | 同一执行原件 → 事后诊断：关键Dispatch的实际恢复/发包时刻、交换机接收到发送的停留时间；不新增网络 |
| `closeout.py` | 完成的运行目录 → 不含受限原件的报告、图、CSV、摘要及命令/资源/身份收据 |

平台代码不导入本目录；这些程序不导入tests/fixture/历史研究驱动。`mechanism.py`是执行后加入的解释工具，不能写成事前预测。逐包详细原件仅保留于本地运行目录。

## 复现条件和运行

需要既有固定依赖、当前平台编译的原生服务、protobuf编译器、frontend Python环境及15份原始文件。这里复用已编译平台，不提供冷环境构建验收；平台安装和构建见[用户指南](../../docs/user_guide.md)。未知远端revision和GPU校准仍未知。首次准备的失败原件与日志保留，未重跑成功网络。

先在干净Git状态执行，使用一个全新任务根。以下示例中`SIM_BACKEND_RECEIPT`是你本次平台编译的身份收据，含`service`绝对路径、`service_sha256`、固定`libraries`路径到SHA256映射，以及`source_hashes`的仓库相对源码路径到SHA256映射；可参考结果的`receipt.json.backend`。不能直接复用他人机器的绝对路径。

```bash
SIM_PY="$PWD/.venv/frontend/bin/python"
SIM_RUN_ROOT="$PWD/runs/clos-transient-new"
SIM_BACKEND_RECEIPT="/absolute/path/to/backend.json"
SIM_PROTOC="/absolute/path/to/protoc"

bounded_step() {
  SIM_STEP_NAME="$1"
  shift
  PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    "$SIM_PY" -B -m moe_ub_cosim.host_probe \
    --output-dir "$SIM_RUN_ROOT/monitors/$SIM_STEP_NAME" \
    --rss-bytes 1073741824 --address-bytes 2147483648 \
    --artifact-bytes 536870912 --wall-seconds 120 \
    --artifact-root "$SIM_RUN_ROOT" --min-free-bytes 8589934592 -- "$@"
}

bounded_step prepare "$SIM_PY" -B experiments/clos_transient/prepare.py \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --routes-root data/moe-trace-small --output "$SIM_RUN_ROOT/prepared"
bounded_step normal "$SIM_PY" -B experiments/clos_transient/run.py \
  --root "$SIM_RUN_ROOT" --backend-receipt "$SIM_BACKEND_RECEIPT" \
  --protoc "$SIM_PROTOC" --case normal
bounded_step local "$SIM_PY" -B experiments/clos_transient/run.py \
  --root "$SIM_RUN_ROOT" --backend-receipt "$SIM_BACKEND_RECEIPT" \
  --protoc "$SIM_PROTOC" --case local
bounded_step analyze "$SIM_PY" -B experiments/clos_transient/analyze.py \
  --root "$SIM_RUN_ROOT" --output "$SIM_RUN_ROOT/analysis"
bounded_step mechanism "$SIM_PY" -B experiments/clos_transient/mechanism.py \
  --root "$SIM_RUN_ROOT" --output "$SIM_RUN_ROOT/mechanism"
```

以上命令按顺序单独执行，任一步失败即停止，不继续粘贴后续命令；运行器亦拒绝在normal未核验时执行local。每个网络条件最多一次，不抬高限额或重试失败网络。所有步骤累计900受监控秒、512MiB；收尾复制产物也计入累计磁盘预算。实际原件与[收尾结果](results/20261030/README.md)分别说明，不能把“参考故障成本”写成“新算法收益”。
