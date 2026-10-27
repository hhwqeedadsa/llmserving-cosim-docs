# 通信准入下界与残留传输

回答两个不同的问题：下一次前端通信最早什么时候可能提交？没有新提交时，网络里是否还有既有包、回复与ACK？范围见[事前协议](protocol.md)，结果见[中文报告](results/20261027/report.md)。普通通用图提供的下界作为强基线，不是新MoE算法。

代码分工：`prepare.py`按固定hash恢复并分离`observed/`与`truth/`；`bounds.py`计算通用AND/OR时间约束及显式NIC最短传播；`predict.py`只读当前观测前缀；`evaluate.py`才读未来记录检验保守性和尾部；`verify.py`独立重数、人工规则及篡改拒绝；`summarize.py`生成报告；`checkpoint.py`保存并恢复离线证据。没有执行新网络或改平台。

已有证据的复算命令如下。所有输出路径须不存在，保留明确的宿主时间、内存与累计磁盘预算；本轮限额见协议。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/admission_bounds/predict.py \
  --observed /path/to/evidence/observed --output /path/to/new-predictions

PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/admission_bounds/summarize.py \
  --stage /path/to/evidence --output /path/to/new-report

PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/admission_bounds/checkpoint.py restore \
  --archive runs/admission_bounds_20261027/checkpoint.tar.gz \
  --output /path/to/new-restored-directory
```

恢复先核成员路径、类型、大小和SHA256，再用复制的源码从空cwd重新预测、评价和生成报告，逐字节对照保存的预测及报告。不运行原生后端，也不要求旧tmpfs存在。大型原件与逐边界结果存于本地`runs/admission_bounds_20261027/`，没有随Git分发；新机器须取得同hash证据包。

本次完整记录不等于硬件校准，最早提交下界不等于空闲证明，离线没有包的区间不等于在线已有安全跳时证书。完整通用图、声明成本、固定请求与冻结动态绑定的限制均须保留。
