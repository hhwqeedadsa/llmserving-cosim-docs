# 大EP分层通信路径核查

本研究补齐当前小场景与较大专家组之间的建模依据。固定源码、真实路由和事前两个源绑定，核对按GPU/服务器域去重、域内转发、Combine归约分支，以及零数据通道结束通知。它不实现Hybrid GPU或包级执行，不把DeepEP已有的分层通信作为创新。

方法及输入取得方式更正见[事前协议](protocol.md)。结果见[网页](results/20261027/report.html)、[报告](results/20261027/report.md)及[去留决定](decision.md)。全部15份完整文件、58层、145232条token×层行都保留，没有复制请求填满EP组；EP64/128/256是离线部署投影，千卡仅作EP与副本数的算术说明。

结果说明：不同源绑定及prefill/decode下，Hybrid相对按目标GPU发送的跨机隐藏向量份数减少约46.7%～46.9%（EP64）、27.5%～27.6%（EP128）、14.9%～15.0%（EP256）。这些是已有通信算法在本组数据中的需求差额，不是新方法、全网线上字节或时延收益。域内转发和部分控制开销另外列出。

独立位图/逐expert模型复核六条件，并拒绝五类篡改；六组真实prefill前缀支持单通道必要依赖子图检查。子图不是完整可执行流程，不能直接用于推断实际GPU重叠。复制源码、空cwd的离线恢复须保持分析、核验及报告字节一致。详细收据见[核验](results/20261027/verification.json)、[恢复](results/20261027/recovery.json)和[资源](results/20261027/closeout.json)。

首次准备误以为ECMP归档含全部15份原文，实际只有8份；失败、原源码和部分复制保留。其余7份从本地读取，并逐项匹配原已固定清单的hash，未更换输入、参数、阈值或预算。原`execution.json`保持failed，后续离线执行另存`offline-continuation.json`。这是数据取得修正，不是重试网络或删除不利结果。

代码入口：`study.py`准备/分析，`verify.py`独立核验，`report.py`生成报告；`run.py`执行有界离线阶段，`checkpoint.py`与`finish.py`完成逐成员哈希恢复和收尾。原件位于`runs/hybrid_path_audit_20261027/`。新研究已关闭，不重新执行`run.py`；已有证据包可离线复核：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m moe_ub_cosim.host_probe \
  --output-dir runs/hybrid-review-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 134217728 --artifact-root runs/hybrid-review \
  --wall-seconds 60 --min-free-bytes 134217728 -- \
  python3 -B -m experiments.hybrid_path_audit.checkpoint restore \
  --archive runs/hybrid_path_audit_20261027/checkpoint.tar.gz \
  --sha256 "$(python3 -c 'import json; print(json.load(open("experiments/hybrid_path_audit/results/20261027/checkpoint.json"))["sha256"])')" \
  --output runs/hybrid-review
```

输出目录必须不存在；恢复仅运行分析/核验/报告，不构建、不运行网络或GPU。预算沿用600受监控秒/384MiB累计产物，单命令60秒/RSS1GiB/地址2GiB，磁盘保留128MiB。全部监控关闭后的[最终资源核查](results/20261027/final-resources.json)另外计入外层及复制收据，不将嵌套计时当独立纯CPU测量。两个论文创新及整体目标继续进行，下一步实现须另行登记范围和验收。
