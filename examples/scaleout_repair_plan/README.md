# 先检查备用路径能否安全转发

当交换机剔除坏出口后没有原有候选时，不能随便选一个其他邻居：对方按旧路由可能把包送回来。本例用独立128端点Clos输入检查普通LFA（无环备用下一跳）的覆盖，并规划必须显式经过中间交换机的修复段。它不运行ns-3，不改变现有路由或预测时延，不需要GPU、模型或真实路由文件。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B -m moe_ub_cosim scaleout topology \
  --config examples/scaleout_repair_plan/clos-128.json --output /path/to/new-topology
.venv/frontend/bin/python -B -m moe_ub_cosim scaleout repair-plan \
  --topology /path/to/new-topology --src 128 --dst 132 --output /path/to/new-plan
```

输出目录必须不存在。支持128/256/512端点、单plane0、每GPU单实际NIC、最多256交换机/16384边；当前实际公开验收为128端点静态规划。选择一条实际交换机边，要求src<dst；检查两端至全部GPU的转发。GPU类型标签不参与计算，此命令没有GPU性能模型。用改过的生成配置替换本例可改变交换机数量和带宽；本规划沿用原生hop距离，带宽不会自动变成路由权重。

`summary.json`区分：不受该出口影响、原ECMP仍有出口、普通LFA可用、需要显式修复段和分区。`plan.json`保存各目的原候选、LFA不等式、显式段/端口、合流点及全部旧ECMP后缀；`manifest.json`保存输入与结果SHA-256。所有输出均为`planned_not_executed`。

普通LFA使用故障前拓扑的严格条件`D(N,D) < D(N,S)+D(S,D)`。等号也不安全：邻居可能有一条旧最短路经过S。显式段使用删边图找路，到达合流点后，所有旧最短路都必须避开坏边和保护点S；只挑一条好后缀不够。参考[RFC5286 §3.1](https://www.rfc-editor.org/rfc/rfc5286.html#section-3.1)和[RFC7490 §2](https://www.rfc-editor.org/rfc/rfc7490.html#section-2)，本命令不是完整Remote-LFA、MPLS或TI-LFA实现。

在此输入中，Agg132到GPU1的原出口只有ToR128。普通LFA不覆盖；规划的显式段为132→129→133，到133后按旧表133→128→1转发。**不能仅在132把包送给129就称为成功修复**：129仍可能送回132，必须实际携带并执行显式段。后续接入还需计算真实线上头部、队列和序列化成本，保留原QP/ACK及丢包恢复；本例没有执行或声称性能收益。功能核验见[独立检查](../../fixtures/link_repair_plan/README.md)。

需要实际执行时，另用[显式修复段执行例](../scaleout_explicit_prefix/README.md)的可选后端模式；本命令及本页的静态结果仍不提供网络执行或收益证据。
