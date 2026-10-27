# EP粒度、真实热点与仿真创新筛选

[方法与预算](protocol.md)、[报告](results/20261027/report.md)、[带图网页](results/20261027/report.html)、[独立SVG](results/20261027/ep_projection.svg)。目标是区分真实专家路由在小/大EP中的网络含义，并约束两个仿真研究候选，不把离线统计当作包级性能或论文收益。

15个完整原件覆盖693个Prefill token、1811个Decode步骤。固定四个源rank、B4/B8分组、连续等容量专家放置，离线比较EP4～256。相同来源下，大EP的均匀随机与真实目的聚集差异较大，但每请求边际热度已解释大部分偏差。统计中的碰撞是不同token共享目的rank，不是已经发生的丢包或交换机拥塞。

`study.py`负责逐token投影、均匀无放回解析参考与控制模板计数；`marginals.py`保留每请求边际热度和循环顺序，计算独立循环平移参考；`verify.py`独立用位图、两两交集、有理数和小宇宙完整枚举核对。准备、分析与报告都不调用网络或GPU。

原件、来源副本、实际命令与资源日志在本地忽略目录 `runs/ep_network_projection_20261027/`。归档含用户提供的原始路由，只在本地保留，不随Git发布。独立恢复需要现有Python环境与Matplotlib，版本见`key_metrics.json`；不安装或下载依赖，不需要旧ns-3程序、旧绝对路径或外部账号。

从仓库根目录恢复到不存在的新目录，使用明确预算：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 \
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir runs/my-ep-projection-restore-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 134217728 --wall-seconds 60 \
  --artifact-root runs/my-ep-projection-restored --min-free-bytes 8589934592 -- \
  python3 -B -m experiments.ep_network_projection.checkpoint restore \
  --archive runs/ep_network_projection_20261027/checkpoint-formatted.tar.gz \
  --output runs/my-ep-projection-restored
```

恢复先检查归档与每个成员hash，再从复制源码和空工作目录重算投影、两种参考、独立核验、中文报告、HTML和科学图，要求逐字节一致。它不运行网络，不把原始文件末尾当作EOS，也不复制请求凑更大batch。

当前交付归档为`checkpoint-formatted.tar.gz`，修正了Matplotlib SVG行尾空白，未改变统计或PNG。旧报告与`checkpoint.tar.gz`原样保留，归档身份以`results/20261027/checkpoint.json`为准。重新归档时可用`checkpoint create --report-directory`选择新生成的报告目录，包内仍统一保存为`run/report`，避免覆盖原始证据。

只检查既有准备目录时，可在同样host_probe预算内调用：

```bash
PYTHONPATH=python:. python3 -B -m experiments.ep_network_projection.verify \
  --run runs/ep_network_projection_20261027 --output runs/my-ep-projection-check.json
```

当前现代符号规划rank上限32、完整MRT1原生桥仅2～4rank。报告中的EP8～256是需求投影，R64～256的控制操作只是机械延用直接模板的条件代数计数；不表示这些规模已运行，更不能用千卡拓扑替代千卡完整调用。
