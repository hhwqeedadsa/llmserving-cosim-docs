# 优化后强基线：还能优化哪里

同一真实路由输入、共享上联网络和完整通信调用；四次观测的网络、control及应用与旧结果一致，仅事前按库名多重集合对应加载元数据。CPU是宿主工作量，不是GPU或网络仿真时延。

| 条件 | 旧native CPU s | O3 native CPU s | O3 Python CPU s | O3 native占比 | O3 Run exclusive占总CPU | 全部native免费CPU工作量倍数 |
|---|---:|---:|---:|---:|---:|---:|
| batch4-modern | 4.7108 | 0.9620 | 0.7722 | 55.47% | 3.67% | 2.246 |
| batch4-generic | 4.7077 | 0.7787 | 0.5854 | 57.08% | 3.73% | 2.330 |
| batch8-modern | 7.9150 | 1.3021 | 1.1696 | 52.68% | 3.88% | 2.113 |
| batch8-generic | 7.9198 | 1.2223 | 0.9828 | 55.43% | 4.15% | 2.244 |

这些是每条件一次、包含逐记录刷新开销的CPU观测，不能作稳定墙钟速度排名。O0与O3差异来自常规构建修复，不是新算法贡献。全部native免费上限保持Python工作不变、同时免除初始化/校验/报告等所有原生成本；它不是纯包优化或墙钟/GPU加速上限。

Run exclusive仍混合网络、传输、内存和事件分派；部分包引发的推进/通知计在其他阶段，因此也不能把该比例当所有包相关优化的严格上限。

主要exclusive阶段（CPU秒）：

batch4-modern / worker：python_candidate_validate=0.27670；controller_advance=0.19008；python_candidate_copy=0.13872；modern_report_write=0.04347。
batch4-modern / native：native_total=0.25586；session_candidate_validate=0.17972；session_progress=0.16378；simulator_run=0.06367。
batch4-generic / worker：python_candidate_validate=0.26988；python_candidate_copy=0.12610；controller_advance=0.08328；wake_conditions=0.02806。
batch4-generic / native：native_total=0.25088；session_progress=0.13525；session_candidate_validate=0.10817；simulator_run=0.05087。
batch8-modern / worker：python_candidate_validate=0.45901；controller_advance=0.26656；python_candidate_copy=0.22081；wake_conditions=0.05969。
batch8-modern / native：native_total=0.27765；session_candidate_validate=0.23889；session_progress=0.22861；simulator_run=0.09590。
batch8-generic / worker：python_candidate_validate=0.46617；python_candidate_copy=0.22646；controller_advance=0.13295；wake_conditions=0.05029。
batch8-generic / native：native_total=0.27588；session_progress=0.22445；session_candidate_validate=0.19927；simulator_run=0.09148。

优先级应按优化后的主导阶段和完整流程成本确定。不能继续用旧80%—86%的原生占比承诺包级算法收益；减少包处理还必须证明多跳、排队、反馈和应用完成保持。四活动rank/单层/便利样本和声明成本不提供千卡或现代硬件性能泛化。两个论文候选仍未成立。
