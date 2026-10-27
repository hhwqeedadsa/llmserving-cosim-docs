# 包级物理交互范围核算

本实验回答：MoE通信的哪些连接共享物理端口/缓冲，实际排队由谁占用串行器造成，以及这些结果是否支持继续研究包级成本缩减。范围见[事前协议](protocol.md)，结果见[报告](results/20261027/report.md)。这是事后诊断工具，不是在线划分算法或千卡加速器。

后续[活跃队列前缀核查](busy_prefix_README.md)独立检验只用当前观测能规划多少连续发送，明确区分局部回调数量、全程乐观界与尚未测得的原生速度收益。

`prepare.py`从固定hash持久包只恢复两组已有详细参考；`analyze.py`核对完整包路径、出口FIFO/占用积分及等待来源；`verify.py`独立重数并检查六类损坏证据。没有发起新网络、运行测试驱动或修改平台。数据和运行包在`runs/interaction_scope_20261027/`，不随Git分发。

已有证据的离线复算（输出目录必须不存在）：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/interaction_scope/summarize.py \
  --stage /path/to/interaction-evidence --output-dir /path/to/new-analysis
```

恢复原始公开记录和本轮分析源码，并在空工作目录复算报告：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/interaction_scope/checkpoint.py restore \
  --archive runs/interaction_scope_20261027/checkpoint.tar.gz \
  --output /path/to/new-restored-directory
```

恢复先核成员路径、类型、大小及SHA256，只执行离线Python分析，不执行原生网络程序。复算一致支持本次保存记录中的事实，不证明在线近似安全、硬件准确或仿真加速。独立检查用`verify.py --stage <原证据目录> --repository <匹配仓库>`，并核对已固定源码身份。

报告中的包等待是累计值；ACK贡献与同连接/跨连接分类重叠。物理rail分组不等于完整应用可以独立运行；NIC内部排队、跨域GPU资源、未来到达/阈值界和随机数语义须另行处理。
