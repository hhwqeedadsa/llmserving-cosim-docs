# 去重与卡内合并后的真实路由局部故障

本目录在128端点Clos上，检验15份真实R1-AWQ/MMLU专家选择驱动的局部接收故障。两个EP64组共用一次原生RDMA ns-3网络，真实LLMServingSim选batch、ASTRA执行依赖。两条件均按目的rank去重、卡内合并、SR和参考DCQCN；只是local注入前100us的目的数据丢弃。场景、竞争解释、指标及限制见[事前方法](protocol.md)。

`prepare.py`从原件目录及来源intake生成可迁移快照、拓扑、两个协议及冻结任务图，独立按原JSON核算布局字节和专家工作。旧四份来源确认按既有manifest路径/SHA核对，新增来源按intake确认；远端revision仍未知。没有克隆请求，不能称全卡满载或完整R1。

`run.py`只调用公开CLI，检查干净版本/冻结输入/方法及后端库身份，拒绝重复尝试；normal通过后才允许local。`analyze.py`读取实际执行快照、原JSON和完整包，不导入测试、fixture或历史实验驱动；产出消息/端点/阶段/成员表、累计到达曲线和网页。卡内合并成本为声明，不是GPU测量。

## 操作

需要当前已验收的原生服务、frontend环境、protoc和15份原件；不提供冷环境构建或数据下载。所有步骤使用新根目录并计入同一900秒/512MiB预算，任一步失败停止，不重跑成功网络。原生单次60秒/RSS1GiB/地址2GiB/模拟5ms/128MiB产物/32MiB结果/完整包200000条；零构建、下载、安装、GPU和依赖修改。

```bash
SIM_PY="$PWD/.venv/frontend/bin/python"
SIM_TASK_ROOT="$PWD/runs/token-transient-new"
SIM_BACKEND_RECEIPT="/absolute/path/to/backend.json"
SIM_PROTOC="/absolute/path/to/protoc"

bounded_step() {
  SIM_STEP_NAME="$1"
  shift
  PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
    "$SIM_PY" -B -m moe_ub_cosim.host_probe \
    --output-dir "$SIM_TASK_ROOT/monitors/$SIM_STEP_NAME" \
    --rss-bytes 1073741824 --address-bytes 2147483648 \
    --artifact-bytes 536870912 --wall-seconds 120 \
    --artifact-root "$SIM_TASK_ROOT" --min-free-bytes 8589934592 -- "$@"
}

bounded_step prepare "$SIM_PY" -B experiments/token_transient/prepare.py \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --routes-root data/moe-trace-small --output "$SIM_TASK_ROOT/prepared"
bounded_step normal "$SIM_PY" -B experiments/token_transient/run.py \
  --root "$SIM_TASK_ROOT" --backend-receipt "$SIM_BACKEND_RECEIPT" --protoc "$SIM_PROTOC" --case normal
bounded_step local "$SIM_PY" -B experiments/token_transient/run.py \
  --root "$SIM_TASK_ROOT" --backend-receipt "$SIM_BACKEND_RECEIPT" --protoc "$SIM_PROTOC" --case local
bounded_step analyze "$SIM_PY" -B experiments/token_transient/analyze.py \
  --root "$SIM_TASK_ROOT" --output "$SIM_TASK_ROOT/analysis"
```

按顺序单独执行，先核查上一步退出状态。后端身份收据需有`service/service_sha256/libraries/source_hashes`；不能直接沿用其他机器的绝对路径。布局修正和成熟恢复方式不是论文创新；故障代价、可消除空间和真实业务收益分别判断。
