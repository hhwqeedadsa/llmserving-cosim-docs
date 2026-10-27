# 活跃队列前缀的局部批处理核查

范围见[事前协议](busy_prefix_protocol.md)，结果见[中文报告](results/busy_prefix_20261027/report.md)。这是只使用当前捕获前缀的局部发送规划器，不是原生ns-3加速功能。保守规则在两份已保存正常记录中的发送身份、顺序和时刻一致；全部共享状态/副作用的等价性仍未证明。

`busy_prefix.py`维护当前FIFO和已在途物理传输；`busy_study.py`负责hash恢复、逐事件规划、当前快照补采、未来记录评价与报告；`busy_verify.py`独立核对计数、人工反例、最优上界和篡改拒绝；`busy_checkpoint.py`恢复后从复制源码/空cwd重算。程序不导入测试驱动，不执行新网络。

已有证据可直接复算，所有输出目录须不存在：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/interaction_scope/busy_study.py report \
  --stage /path/to/busy-evidence --output /path/to/new-report

PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  experiments/interaction_scope/busy_checkpoint.py restore \
  --archive runs/busy_prefix_20261027/checkpoint.tar.gz \
  --output /path/to/new-restored-directory
```

从持久参考准备新离线分析依次使用`busy_study.py prepare --repository <仓库> --stage <新目录>`、`predict --observed <新目录>/observed --output <新目录>/predictions`、`snapshots --stage <新目录>`、`report --stage <新目录> --output <新报告>`。补采快照时重新遍历观测，原有规划文件必须逐字节不变；没有重跑网络。每命令及累计资源限制见协议。

未来`truth/`只供评价；删除truth仍能产生相同规划与当前快照。`queued_upper`是历史代码标识，表示忽略截止的固定贪心参考；全程最优上界另存为`evaluation_only_fixed_FIFO_prefix_upper`，明确使用未来全部快照，不是可在线采用的算法。

大型输入/逐包证据存于本地`runs/busy_prefix_20261027/`，不随Git分发。恢复先检查归档及每成员SHA256、类型、路径和大小，再从复制源码/空cwd预测、补采快照和生成报告，全部逐字节对照；不要求原tmpfs或原生运行时存在。
