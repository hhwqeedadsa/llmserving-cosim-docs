# 恢复粒度与实际执行依赖

在128端点Clos早备用强基线之后，核对三个超时输入、完整备用工作和全部目标请求的依赖，判断哪些成本有希望减少、哪些恢复单位当前不可执行。方法见[执行前约定](protocol.md)。本研究只读已保存结果，不改平台、不执行新网络，不把账面子集成本当成等价任务加速。

在仓库根目录、已有前端环境及前轮原件完整时使用，输出目录须不存在，事前落实600秒/256MiB累计预算及8GiB磁盘余量：

```bash
PYTHONPATH=python .venv/frontend/bin/python -B experiments/recovery_scope/checks.py

PYTHONPATH=python .venv/frontend/bin/python -B experiments/recovery_scope/prepare.py \
  --study runs/ack_guard_whole_20261030/study \
  --output runs/recovery_scope_20261030/input

PYTHONPATH=python OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B experiments/recovery_scope/analyze.py \
  --frozen runs/recovery_scope_20261030/input/frozen.json \
  --output runs/recovery_scope_20261030/analysis
```

`prepare.py`固定原网络逐文件身份，并将实际启动DECIDE之前的源发送/确认前缀单独导出。正常以相同绝对时刻截断；同纳秒原生顺序保留。`source_status`只读初始图及上述源前缀，不读接收真值或后续结果。事后分析才读取完整记录、原JSON、接收和节点时刻。

`dependencies.py`保留原节点前驱，将全EP门限展开为全成员本地前驱的合取，再将实际跨步图退休要求加入下一步根。它只证明结构可达性；不估计修改后的网络时延、不把SEND确认当RECV前驱。小型人工CPU图只验证计算规则，不替代128端点网络证据。

逐请求工作量从原JSON专家选择、声明成本和传输布局独立重建，与实际每条消息/每rank计算核对；已有真实路由不代表激活/权重/数值等价已验证。当前消费者只接收完整片段结果，部分贡献与部分请求方案需要真实应用消费接口后才能评价恢复性能。
