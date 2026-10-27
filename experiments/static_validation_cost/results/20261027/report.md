# 固定校验复用：完整流程省了多少

四个固定条件，每条件三对交错运行。CPU与墙钟覆盖完整命令及其已等待子孙，速度对照关闭细粒度计时日志。默认/复用均逐项保持旧完整网络、控制消息与应用结果。

| 条件 | CPU倍率中位数 | 三对CPU范围 | 墙钟倍率中位数 | 三对墙钟范围 | 达到事前工程阈值 |
|---|---:|---|---:|---|---|
| batch4-modern | 0.985 | 0.830—1.088 | 1.003 | 0.830—1.093 | 否 |
| batch4-generic | 1.009 | 0.940—1.147 | 1.004 | 0.913—1.096 | 否 |
| batch8-modern | 1.050 | 1.044—1.069 | 1.054 | 1.049—1.087 | 否 |
| batch8-generic | 1.010 | 0.863—1.148 | 0.983 | 0.893—1.124 | 否 |

倍率=默认耗时÷复用耗时。1.10倍约表示耗时减少9.09%，不是减少10%；小于1表示变慢。工程阈值为三对CPU均>1.05且中位数≥1.10；不是统计显著性或论文新颖性门槛。三对不足以给跨机器稳定性能保证，全部原数与RSS见summary.json。

完整现代公共CLI包含其内层进程监控，通用执行器使用公开session并由外层监控；每条件内两模式开销策略一致，不能只比较二者CPU绝对值便归因于语义表示。RSS是10Hz采样进程树和，包含测量包装进程；wait4 maxrss是最大单进程峰值，两者口径不同。

另四次开启复用并写细粒度日志，仅解释剩余开销，不混入上表速度对照：

| 条件 | native占已观测CPU | Run exclusive占已观测CPU | 所有native免费CPU工作量界 |
|---|---:|---:|---:|
| batch4-modern | 61.15% | 4.25% | 2.574 |
| batch4-generic | 56.32% | 3.66% | 2.290 |
| batch8-modern | 56.22% | 4.21% | 2.284 |
| batch8-generic | 62.13% | 4.62% | 2.641 |

batch4-modern / worker：python_candidate_validate=0.19303s；controller_advance=0.17243s；python_candidate_copy=0.12338s；modern_report_write=0.03677s。
batch4-modern / native：native_total=0.25642s；session_candidate_validate=0.18447s；session_progress=0.17631s；simulator_run=0.07038s。
batch4-generic / worker：python_candidate_validate=0.25552s；python_candidate_copy=0.15239s；controller_advance=0.10949s；wake_conditions=0.03848s。
batch4-generic / native：native_total=0.27753s；session_progress=0.13700s；session_candidate_validate=0.11569s；simulator_run=0.05432s。
batch8-modern / worker：python_candidate_validate=0.33007s；controller_advance=0.24711s；python_candidate_copy=0.20428s；wake_conditions=0.05259s。
batch8-modern / native：native_total=0.27315s；session_candidate_validate=0.23110s；session_progress=0.23041s；simulator_run=0.09607s。
batch8-generic / worker：python_candidate_validate=0.36005s；python_candidate_copy=0.20911s；controller_advance=0.13053s；wake_conditions=0.05018s。
batch8-generic / native：session_candidate_validate=0.29798s；native_total=0.26674s；session_progress=0.24183s；simulator_run=0.10432s。

Run混合网络、内存、传输与事件分派，不是纯包CPU。所有native免费假定Python成本不变且所有原生工作均可消除，只是宽松CPU工作量界，不是墙钟或实际算法收益。

输入为八份真实MMLU路由的便利样本、单层EP4、batch4/8及声明计算/provider成本；没有GPU执行、生产硬件校准或千卡外推。固定图来自旧实际绑定，不能称输入独立编译器。普通校验复用属于强基线工程，两项论文创新仍需新的机制和收益证据。
