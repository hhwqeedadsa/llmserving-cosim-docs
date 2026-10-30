# 无环备用和显式修复段规划验收

公开入口见[独立128Clos示例](../../examples/scaleout_repair_plan/README.md)，需求见[规范](../../docs/tasks/link_repair_plan_spec.md)，可读结果见[网页](report.html)，独立检查见[verification.json](verification.json)，来源及资源见[receipt.json](receipt.json)。本轮没有运行网络、改写Fabric路由、模拟标签或声称收益。

公开topology与repair-plan从本例配置生成全新目录，不依赖真实路由账户或旧构建。输入为128端点、20交换机、单plane/单NIC，保护128–132。检查两端至每个GPU，共256个转发组合；不是256卡实验。128个组合不受该出口影响，112个仍有原等价出口，16个原出口全部失效且普通LFA不能覆盖。后16个需要显式段132→129→133，到133后全部旧ECMP后缀可安全转发；普通LFA不覆盖不等于物理分区。

平台使用BFS距离及旧转发DAG安全判断。独立audit不导入平台，用Floyd-Warshall、禁止GPU作为中间点，检查全部256个组合、LFA两侧、删边路径长度/实际端口、首个安全合流点和全部旧ECMP后缀，拒绝四类篡改：把等号判安全、选不安全合流点、虚构物理端口及虚构覆盖。9项单位检查另覆盖可用LFA、原ECMP顺序、分区、GPU中转拒绝、非法输入在输出前失败、多NIC/多plane/规模限制及独占写出。小图只用于CPU算法检查。

首次单位检查的一条断言误把按源节点排序的后缀边当成路径顺序，8项中1项失败；修正为核对完整边集合并补规模边界检查后9项通过。原失败日志保留，不改算法或放松无环要求。相关旧拓扑/链路输入检查另行运行，无原生构建或网络重跑。

首次制图在1GiB地址空间限制下遇到默认多线程OpenBLAS初始化分配失败，已保留原脚本和日志。将制图的BLAS线程限定为1后完成，预算没有提高；已写出的三个证据文件逐hash核对后复用，不重做规划或网络。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B -m unittest discover -s tests/unit -p scaleout_repair_plan_test.py
.venv/frontend/bin/python -B fixtures/link_repair_plan/audit.py \
  --stage /path/to/retained-stage --output /path/to/new-verification.json
```

本机原件根`runs/link_repair_planning_20261030`，含公开命令/输出、独立证明、监控和公开RFC原文hash。下载的RFC5286/7490共约137KB，用于核对严格不等式及修复隧道/全部等价路径语义，不下载模型或真实数据。没有升级依赖。预算300受监控秒/128MiB产物/卷留8GiB；每阶段60秒/RSS512MiB/地址1GiB。

后续仍需在原生共享网络执行显式修复段：仅本端故障触发、远端按携带的修复状态转发、真实计入头部/队列/序列化、不中断QP/ACK、不复活已丢数据，并与旧默认结果对照。当前尚未完成这些步骤，不是Remote-LFA/MPLS/TI-LFA实现，也没有新增MoE时延收益或硬件结论。
