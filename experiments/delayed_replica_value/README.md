# 固定门槛的延迟完整副本对照

[可视化报告](results/20261030/report.html)比较同一128端点Clos上的无副本、立即副本和70微秒延迟副本。正常延迟副本与无副本逐包一致；本次局部故障仅缩短3.735微秒（2.73%），仍执行双份工作。成熟延迟备用已补齐，但没有证明新算法收益。

约束和执行前判据见[方法](protocol.md)。只使用四份已取得的R1-AWQ/MMLU原件、两个原层及一个Decode步，计算成本为声明；两组模型和等价ready-KV预先存在。完整推理、数值等价、GPU校准、吞吐、独立健康任务和初态建立不在本轮结论内。

## 可复现步骤

1. 按[公开示例](../../examples/scaleout_whole_call/README.md)准备自己的本地来源清单，增加`--hedge-after-ns 70000 --include-primary-control`。使用支持`--control-wakeup`的原生RDMA服务；依赖按仓库锁定版本构建，不依赖本机旧build目录。
2. 准备四份已经通过公开`rdma-check`的无副本/立即副本normal/local控制。执行`prepare.py --help`，填入这四个目录、准备输入和后端，冻结新输出根中的`frozen.json`。新normal/local目录须不存在。本轮冻结清单的绝对路径是收据，迁移时应重新冻结自己的输入，不照抄本机路径。
3. 按公开`rdma-run`分别运行`service-delayed.json`与normal/local协议，输出到新normal/local目录；按事前预算各一次，失败保留，不自动重试。
4. 用`analyze.py --frozen ... --normal ... --local ... --output ...`独立核对原始来源、完整图、节点、门限、接收/确认、源包、原生定时与KV。分析只读取已有结果，不重新执行网络。
5. 用`plot.py --frozen ... --analysis /path/to/analysis.json --normal ... --local ... --output ...`导出两张图。它只通过公开`rdma-arrivals`重建三个第一层Dispatch切片，不执行新网络。图形使用已有Matplotlib环境。
6. 用`report.py --analysis /path/to/analysis-directory --frozen ... --figures ... --output ...`生成带图的便携网页与曲线CSV。

结果中的源包和KV数字是本次有限实验的资源记录，不能当作生产部署经济收益。每种条件只执行一次，不产生统计置信区间；70微秒没有寻优。
