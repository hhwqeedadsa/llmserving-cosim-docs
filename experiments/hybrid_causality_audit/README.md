# Hybrid单转发器：静态因果表示的适用边界

这项研究检验：固定DeepEP的进度发布和消费上限均为6时，普通静态任务图能否表达单转发器的数据块顺序、阻塞来源和结束条件。不是完整Hybrid、网络或GPU性能实验。

- [事前协议与预算](protocol.md)
- [中文网页](results/20261027/report.html)、[报告文本](results/20261027/report.md)
- [研究取舍](decision.md)
- [核验摘要](results/20261027/verification.json)、[指标](results/20261027/metrics.json)

平台固定`19670ba`，DeepEP固定`f6aa38fe06cd4a0489e7730365d4e0114970a2f3`。1527根条件覆盖1,049,283联合状态、2,711,060条非平凡转换；14次公开plan、42次公开逻辑replay全部完成。保留tail/finish的普通DAG与缓存状态机在本次覆盖中一致。5/6粒度抽象控制得到不同块边界，不能不检查适用条件。没有新的推理收益或仿真加速数字。

## 输入与代码

`study.py`准备和核hash真实原件/固定源码、声明环境，预测仅读`observed/`。15完整文件取entry0/层3的最多16个原token，绑定rank0～14；EP16中rank15为空。只回放入口0/通道0，其源rank0/8的目标域数据数为14/16。不能把其他rank的输入存在写成全部转发器完成。

`models.py`分别转录缓存逻辑与普通DAG，穷举外部发布/消费交错，不导入平台执行器。`verify.py`在评价时读`truth/`，用独立的“块轮次、循环源距离”排序核对DAG，并核查原token、slot、阻塞和完成前缀；六类证据篡改均拒绝。状态/转换hash在恢复中重新计算；它们不是完整CUDA程序的形式化证明。

`run.py`通过公开`scaleout hybrid-deepep-plan/replay`生成计划和逻辑回放，不导入测试或fixture。`report.py`生成中文报告。`checkpoint.py`保存逐成员hash，复制源码、空cwd离线重算三个穷举结果/预测/核验/报告，既不重跑公开回放也不启动网络。

## 重现方式

本机原件与所有命令/日志在`runs/hybrid_causality_audit_20261027/`。新准备依赖既有已核hash的15份原件与六份库源码；它们是本地来源数据，不在Git中。用户已确认17份原始文件均来自`core12345/MoE_expert_selection_trace`的`cognitivecomputations/DeepSeek-R1-AWQ/mmlu`目录，899/1479因缺层排除。HF远端修订未独立核实。

首次执行分为`prepare`、`plans`、`environments`、`predict`、`exhaustive-2`、`exhaustive-3`、`exhaustive-4`、`replays`。每阶段一次，输出目录须新建；累计600受监控秒/256MiB、单命令60秒/RSS1GiB/地址2GiB、保留128MiB空闲。实际命令都保存在`execution.json`及`monitor/*/probe.json`。本轮额度已经关闭，不重复运行已完成条件。

如有一个独立批准的新研究预算，可从仓库根目录按下面形式运行一个尚未执行的阶段，不对关闭的目录复用命令：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/NEW_STUDY/monitor/prepare \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 268435456 --wall-seconds 60 \
  --artifact-root runs/NEW_STUDY --min-free-bytes 134217728 -- \
  python3 -B -m experiments.hybrid_causality_audit.run prepare \
  --repository /ABSOLUTE/REPOSITORY --run /ABSOLUTE/REPOSITORY/runs/NEW_STUDY
```

自包含检查点为`runs/hybrid_causality_audit_20261027/checkpoint.tar.gz`，身份及恢复结果见同目录`checkpoint.json`、`recovery.json`。恢复器要求明确archive SHA256，拒绝不安全路径、符号链接、重复成员、超限和内容hash错误；使用包内源码离线重算，不要求旧构建、GPU、网络或原始下载目录。已完成的一次恢复不自动授权再次执行。

结果只能支持本研究的逻辑范围。两项论文创新与整体目标仍进行中；跨转发器slot竞争、完整Combine、真实轮询成本、匹配GPU/GIN/NVLink校准及千卡共享网络尚未由本轮验证。
