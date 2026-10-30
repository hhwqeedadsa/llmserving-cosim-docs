# 局部短时故障的真实路由研究

目的与控制变量见[事前方法](protocol.md)。使用实际LLMServingSim/ASTRA与同一个RDMA ns-3网络，不是另写执行器重放保存图。8份原件来自用户确认的R1-AWQ/mmlu目录，远端revision未知；原始文件不随Git分发。准备时按intake清单选定前8份合格原件，每份只用一次。

使用已有前端Python、protoc、匹配当前程序和库的构建收据；不自动构建/下载。以仓库根为cwd，设置`PYTHONPATH=python`及`PYTHONDONTWRITEBYTECODE=1`。所有命令应由公开`python -m moe_ub_cosim.host_probe`按方法的时间、内存、累计产物及空闲空间预算监控；运行根须是新目录。准备只读来源清单和原件，生成独立输入，不执行历史实验驱动。

下方`python`指已经激活的前端环境；本机为`.venv/frontend/bin/python`。例如把单个网络命令放在以下监控中（其余准备/核验/分析命令同样包装，监控输出目录每次唯一）：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 .venv/frontend/bin/python -B \
  -m moe_ub_cosim.host_probe --output-dir /path/to/new/study/monitors/gbn-normal \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 536870912 --wall-seconds 120 \
  --artifact-root /path/to/new/study --min-free-bytes 8589934592 -- \
  .venv/frontend/bin/python -B experiments/local_transient_real/run.py \
  --root /path/to/new/study --receipt /path/to/current/backend.json \
  --protoc /path/to/protoc --case gbn-normal
```

```bash
python experiments/local_transient_real/prepare.py \
  --intake experiments/route_coverage/results/local_additions_20261027/intake.json \
  --routes-root /path/to/original/routes --output /path/to/new/study/prepared

# 固定代码提交及准备摘要后，按顺序分别执行四个条件，每个最多一次。
python experiments/local_transient_real/run.py --root /path/to/new/study \
  --receipt /path/to/current/backend.json --protoc /path/to/protoc --case gbn-normal
# 接着依次 gbn-local、sr-normal、sr-local；任一失败停止，不提高预算。

python -m moe_ub_cosim scaleout rdma-check --run /path/to/new/study/gbn-normal
# 每个成功条件均执行同一公开检查；两个local条件另执行data-loss-check。
python experiments/local_transient_real/analyze.py \
  --root /path/to/new/study --output /path/to/new/study/analysis
python experiments/local_transient_real/verify.py \
  --root /path/to/new/study --output /path/to/new/study/verification.json
python experiments/local_transient_real/diagnose.py \
  --root /path/to/new/study --output /path/to/new/study/diagnosis.json
python experiments/local_transient_real/render.py --root /path/to/new/study \
  --analysis /path/to/new/study/analysis --diagnosis /path/to/new/study/diagnosis.json \
  --output /path/to/new/study/rendered
```

`run.py`拒绝脏工作区、原件/准备/方法hash改变、程序/库与收据不符、已尝试条件、前条件失败或预算耗尽。失败原件保留；修正离线分析不允许重新运行网络。输出HTML区分整段服务、两个组完成、网络排空；CSV给出逐消息、收发端统计与选定collective的累计有效到达。GBN/SR均是成熟参考机制，本实验不以取得正优化收益作为通过条件。

本轮执行代码固定`3a6a2aa751`，其后只补充离线统计、独立核验和图表，以及未来执行的失败收据保护，没有追加网络。首次独立核验误读源包字段`bytes`，已按公开列名修正为`payload_bytes`，失败日志保留。查看[本轮结果网页](results/20261030/report.html)、[逐包独立核验](results/20261030/verification.json)、[尾部与连接积压诊断](results/20261030/diagnosis.json)及[结果解释](results/20261030/interpretation.md)。原始完整包/任务记录在本地运行根，不随Git分发。
