# 局部恢复的消费与缓冲边界

本项只读审计核对：真实专家选择经过现有通信接口时，哪些数据可交给计算，哪些原传输和缓冲仍不能退出。它不执行新网络，不从依赖图计算加速比。

固定来源目录须含`file-sources.json`及`sources/<原路径>`，现代目录还需`file-sources-completion.json`。来源完整URL、提交和哈希由结果`sources.json`保存，不在线更新。当前流程从已有guarded-local运行的两个wave读取原始选择，准备四份独立EP32输入：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/consumption_boundary/audit.py \
  --run runs/ack_guard_whole_20261030/study/guarded-local \
  --ll-source data/research/deepep_semantics_20261022 \
  --modern-source data/research/deepep_modern_20261022 \
  --output /path/to/new-consumption-audit
```

程序只调用公开`scaleout modern-deepep-plan/check`和`deepep-plan`，保留命令、stdout、stderr和退出码；失败不重试、不扩限。需要在已登记CPU/磁盘预算监控下执行。新机器无需旧账户即可对结果中四份`inputs/*.json`使用相同公开plan/check命令；输入来自既有真实路由的投影，不认证激活或权重。

`analysis.json`保存独立DAG闭包与公开checker对照、旧计划形状拒绝和通知/内存反例；`sources.json`保存核验源码身份和摘录；`frozen.json`记录既有网络文件及输入身份。`checks.py`检查篡改/遗漏等拒绝规则；`report.html`用中文说明能力及待实现接口。详细事前约束见[protocol.md](protocol.md)。
