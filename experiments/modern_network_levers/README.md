# 完整现代调用：带宽与传播时延对照

先读[事前方法与预算](protocol.md)，结论见[中文报告](../../docs/reviews/联合仿真的两个创新候选与网络收益判别_20261026.md)。本研究只调用公开CLI或读取保存JSON，不导入平台控制器、测试或fixture。

三次新网络已完成并关闭。使用四份原始MMLU路由的下标2/层3、每rank一个token、EP4/H7168/E256/Top8；2KB分包，声明计算成本，无GPU硬件校准。

| 网络 | 模型应用阶段 | 全部现代事件/源保护释放 | 网络排空 |
|---|---:|---:|---:|
| 100G、传播1000ns | 57,479ns | 63,523ns | 69,547ns |
| 200G、传播1000ns | 53,446ns | 59,468ns | 65,480ns |
| 100G、传播500ns | 33,503ns | 36,547ns | 39,571ns |

应用阶段改善7.02%/41.71%，三种完成定义的方向一致。传播变化同时作用控制与数据，不归因为单一消息类型；两种网络升级并非同成本算法。控制操作/原子回复占MRT1帧字节4%、源数据包45.45%，均不包含独立ACK和所有物理头部。三组均无重传/CNP命名事件，不能推广为拥塞/故障研究结果。

执行：`run.py --repository ... --stage <不存在的新目录> --manifest data/moe-trace-small/manifest.json --runtime <已核验原生运行时目录> --identity <对应身份清单>`。原始路由下载与来源见清单，不能换文件而沿用旧hash。执行明确限制最多3次网络，失败保存停止；不复用关闭阶段预算。

分析和核验不执行网络：

```bash
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.modern_network_levers.analyze \
  --stage /path/to/results --output /path/to/summary.json
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 python3 -B -m experiments.modern_network_levers.check \
  --stage /path/to/results
```

保存[摘要](results/20261026/summary.json)、[核验](results/20261026/verification.json)、[来源](results/20261026/sources.json)、[恢复](results/20261026/recovery.json)及[收尾](results/20261026/closeout.json)。完整包存 `runs/modern_network_levers_checkpoint_20261026/`，包内保留本轮研究源码与结果；恢复分析无需native运行时。不是干净原生构建或完整千卡验证。原件 `/dev/shm/moe-modern-network-levers-20261026/`。
