# 小规模非 UB Scale-Out 全流程方法

本目录保存小规模网络机制实验的方法、执行与分析脚本。链路为LLMServingSim→依赖图→ASTRA→单一非UB ns-3→完成回调→下一轮服务，以8个GPU逻辑端点研究网络机制。方法审阅和性能结果分别交付。

先读 [8页PDF审阅稿 v0.2](../../docs/review_artifacts/ScaleOut实验方法_审阅稿_v0.2_20261015.pdf)，可编辑原文是 [method_review_v0.2.md](documents/method_review_v0.2.md)。其中列出输入/消息定义、5种小规模拓扑、10个主实验条件、网络与业务指标、可视化及G0—G3验收；明确千卡静态生成、小图逐包执行、声明GPU成本和故障恢复的边界。非UB运行入口见[用户示例](../../examples/scaleout_rdma/README.md)。原 [plan.md](plan.md) 与 v0.1 PDF 保留为实施前的历史方案，其“尚未实现”不表示当前状态。

仅重新导出文档，在仓库根目录运行，输出选择新路径：

```bash
python3 experiments/scaleout_e2e/documents/render_method.py \
  --source experiments/scaleout_e2e/documents/method_review_v0.2.md \
  --revision v0.2 --footer '实验方法与验证边界；性能结果另报' \
  --output runs/scaleout-method-review-new.pdf
```

需要已有的Matplotlib、Pillow和中文TTF/TTC字体；程序不安装或下载。默认使用本机文泉驿微米黑，可用 `--font` 指向其他已存在字体。PDF为A4、嵌入中文字体、文本可搜索，旁边的manifest记录源文/排版程序/字体及PDF的SHA-256。文中的拓扑数量由配置规则推导，不能当作原生装载成功的证据。

v0.2 PDF生成与检查记录在 `runs/scaleout_method_pdf_v02_20261015/`：8页A4、中文字体嵌入、文本可搜索，并逐页预览。本次文档修订未新增构建或网络执行。当前任务统一见 [任务记录](../../docs/tasks/current.md)。

## 已完成实验与证据

[结果与机制说明](conclusions.md)给出30次主运行、独立rail、共享EP4、协议检查和模型变形的实际结果。[离线网页](../../docs/review_artifacts/scaleout_basic_20261015/report.html)内嵌7组图，无需服务器或外部网络；旁边保留PNG、矢量PDF、CSV和JSON。图表均读取实际执行结果。

平台实现本地提交为 `3897d82`。原始矩阵在实施工作树上运行；[版本证据](evidence/versions.json)说明源码、构建、后续零成本修复和独立复跑之间的关系，不用当时HEAD冒充完整源码版本。31次主/rail结果由最终核验器重新检查，42712个节点通过；基线独立重跑result.json和36个语义/Chakra文件逐字节一致。平台与实验分开提交，不push。

## 重新运行

先按[RDMA小例](../../examples/scaleout_rdma/README.md)准备现有工具链、Chakra C++ schema和独立后端；Python环境须已有锁定前端的pandas、rich和protobuf等依赖。下面从仓库根运行，变量改为本机路径。`COSIM_WORK`所在卷至少留8 GiB；运行子目录必须不存在。

```bash
export PYTHONPATH="$PWD/python"
COSIM_PYTHON=/path/to/frontend-venv/bin/python
COSIM_PROTOC=/path/to/protobuf/bin/protoc
COSIM_BUILD=/path/to/rdma-build
COSIM_WORK=/path/to/work-volume/scaleout-new
mkdir -p "$COSIM_WORK"

"$COSIM_PYTHON" experiments/scaleout_e2e/run_matrix.py \
  --backend-json "$COSIM_BUILD/backend.json" --protoc "$COSIM_PROTOC" \
  --output "$COSIM_WORK/matrix"

"$COSIM_PYTHON" experiments/scaleout_e2e/validate_execution.py \
  --runs "$COSIM_WORK/matrix" --backend-json "$COSIM_BUILD/backend.json" \
  --protoc "$COSIM_PROTOC" --output "$COSIM_WORK/validation"

"$COSIM_PYTHON" experiments/scaleout_e2e/diagnostic_controls.py \
  --baseline-run "$COSIM_WORK/matrix/clos-11" --backend-json "$COSIM_BUILD/backend.json" \
  --protoc "$COSIM_PROTOC" --output "$COSIM_WORK/diagnostic"

"$COSIM_PYTHON" experiments/scaleout_e2e/shared_fabric_check.py \
  --backend-json "$COSIM_BUILD/backend.json" --protoc "$COSIM_PROTOC" \
  --output "$COSIM_WORK/shared"

# Use an existing analysis environment with Matplotlib and NumPy.
python3 experiments/scaleout_e2e/analyze.py \
  --runs "$COSIM_WORK/matrix" --diagnostics "$COSIM_WORK/diagnostic" \
  --output "$COSIM_WORK/figures"
```

脚本通过公共 `scaleout` CLI 使用平台，不导入tests或平台内部执行驱动。主矩阵运行时生成全部条件配置和命令记录；调单一条件时修改保存的JSON副本，再用公开 `rdma-run`，不要直接修改结果JSON。可修改范围、单位与错误处理见RDMA小例；调整GPU标签不会自动改变计算性能。

每个 `rdma-run/probe` 内置单例资源监控。主矩阵自身累计结果限256 MiB，建议外层用公开 `python -m moe_ub_cosim.host_probe` 对整个工作目录设置300秒、RSS1 GiB/地址空间2 GiB、累计512 MiB、8 GiB预留；运行会在遇到非预期失败时停下。尾包丢失是预先定义的负例，必须保留其非零退出和未完成记录。预算不足不能靠自动扩限或覆盖旧目录重跑。

## 本轮保存位置与限制

完整命令、原始Chakra/ASTRA日志、结果、失败探针和资源记录已压缩备份到本机持久目录 `runs/scaleout_e2e_20261015/evidence.tar.gz`，旁边manifest逐文件记录hash；小型[归档摘要](evidence/archive.json)纳入版本控制。临时构建位于 `/dev/shm/moe-scaleout-e2e-20261015/native-03/`，不提交二进制，内存盘不能当作持久备份。另一台机器通过上述命令重新构建和运行。

研究范围为8端点、2层4轮ready-KV Decode与合成路由；声明计算成本未校准完整GPU。主EP4包含本地化/副本变化，双plane设备资源不同，3个路由seed不支持生产p99。真实路由、训练、完整HPN协议、机内互联和千卡逐包仿真不在本轮验收内。详细协议缺陷及适用范围见结果说明，阴性结果按实际记录保留。
