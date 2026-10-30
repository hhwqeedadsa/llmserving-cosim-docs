# 已完成结果

2026-10-30：[网页](results/20261030/report.html)、[文字解释](results/20261030/README.md)、[派生数据](results/20261030/analysis.json)、[逐包机制](results/20261030/mechanism.json)、[核验](results/20261030/verification.json)。

四个128端点Clos对照各运行一次，真实专家选择、两层两步。成熟的正常样本校准SR超时将本次局部故障目标片段由189.208µs降到125.977µs；正常121.120µs保持。收益和增加的缓冲峰值见结果说明。这是强基线，不是创新协议。

方法按执行前版本保存在[研究说明](README.md)。`study.py`生成主结果与图，`verify.py`独立核验公开记录，`publish.py`对本次已观测结果补充因果解释并拒绝不同数值的输入。`monitor.py`限制每阶段及累计资源。源路由、大记录及编译产物不提交。
