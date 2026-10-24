# 接收进展与输出恢复：实际网络判别

本实验检验“保留已经收到的owner结果”是否超过通用操作级重试。结果：16次固定网络执行中，owner保留的8个条件与同信息操作级计划全部等价；不能将其作为新恢复机制。详细结论见 [研究报告](../../docs/reviews/接收进展恢复执行与强基线判别_20261024.md)。

公开平台API为 `NativeTransactionSession`；实验不导入tests/fixtures，不修改平台或第三方依赖。准备程序读取原始路由，策略只接收过滤后的目标applied与本地事件，独立检查器读取实际包、内存和控制记录。GPU4为独立结果接收者，专家owner为GPU0—3；只是单token单层的输出交付片段，不是LLMServingSim请求或完整MoE推理。

`protocol.md`保存预登记、串行timer约束及首条件宿主终态分类错误的处置。首网络原结果保留，仅执行一次；核验后继续其余15个未执行条件。没有调整网络参数、扩大预算或重复网络取样。

## 在新目录执行

先确保已有匹配版本的可执行程序、八个固定原生ns-3库、四个已校验路由JSON，以及内存盘至少8GiB空闲。以下变量须指向实际位置；不要安装或升级依赖来绕过版本检查。`prepare.py --library-dir`显式记录实际库位置；执行器仍须通过自身RPATH或既有加载配置加载这些库，检查器验证结果中的实际加载路径。

```bash
cd /home/lry/moe-ub-cosim
export PYTHONPATH="$PWD/python"
export PYTHONDONTWRITEBYTECODE=1
# 将这三个值填写为本机的新输出目录、固定session程序和八库目录。
study_stage=/dev/shm/recovery-execution-new
study_backend=/absolute/path/to/ns3.42-moe-rdma-transaction-session
study_libdir=/absolute/path/to/native/lib
python3 -B experiments/recovery_execution/prepare.py \
  --repo "$PWD" --stage "$study_stage" --backend "$study_backend" --library-dir "$study_libdir"
python3 -B experiments/recovery_execution/run.py --repo "$PWD" --stage "$study_stage"
python3 -B experiments/recovery_execution/audit.py \
  --stage "$study_stage" --output "$study_stage/independent-audit.json"
python3 -B experiments/recovery_execution/summarize.py \
  --audit "$study_stage/independent-audit.json" --output "$study_stage/comparison.json"
```

正式执行时须将准备、矩阵和离线检查分别放入公开`python -m moe_ub_cosim.host_probe`，显式给出60秒、RSS1GiB、地址2GiB、64MiB产物及8GiB预留，例：

```bash
python3 -B -m moe_ub_cosim.host_probe \
  --output-dir "$study_stage/matrix-monitor" \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 67108864 --wall-seconds 60 \
  --artifact-root "$study_stage" --min-free-bytes 8589934592 \
  -- python3 -B experiments/recovery_execution/run.py --repo "$PWD" --stage "$study_stage"
```

矩阵内部为每个控制进程及原生子进程另设同样限额；全阶段仍须遵守300秒/192MiB累计预算。输出目录已存在时拒绝覆盖；失败立即停止。`--continue-unexecuted`只用于本轮已经独立核验的首宿主分类错误，不是通用失败重试开关。

## 离线核验

独立检查器仅需Python标准库。复制完整证据目录（含`raw/`、`inputs/`、`cases/`、`monitors/`和`manifest.json`），即可在新路径执行`audit.py`与`summarize.py`。它优先使用包内路由原件，不依赖源机器原始数据路径。检查器重建连续前缀接收、MRT1帧、接收与规范输出区、源租约、实际选路及端口服务量，同时拒绝三类篡改。

本轮持久包入口在 `runs/recovery_execution_checkpoint_20261024/README.md`。包内另附前一阶段的平台压缩检查点以恢复匹配程序/库；离线统计重算与重新运行原生网络是两种不同验证，不混称。

版本化的[验收摘要](results/20261024/verification.json)、[独立核验](results/20261024/independent-audit.json)和[匹配对照](results/20261024/comparison.json)可直接审查。持久包SHA256为`5a025ae2e420982e2dbcae0e12a2855cd8a6d7097b8e49d563e277945e5d8707`；279成员，23,280,407B。README此处的归档身份为归档生成后附录，包内版本的结论及代码不变。
