# 真实请求路由的时间对齐

判断每请求平均热度相同时，请求间同步突发是否还带来值得细化建模的网络等待。范围、竞争解释、收益门槛和停止规则见[事前协议](protocol.md)。本轮严格停于首个检查器失败；修正后仅离线核验已有结果，没有完成32条件对照。

[报告](results/20261027/report.md)、[网页](results/20261027/report.html)与[摘要](results/20261027/summary.json)区分了一个实际网络结果和32个冻结预测。实际结果只来自 `core100/aligned/phase0`：八卡单层通信字节投影、八轮、434次WRITE和136个本地节点，共310.233us。A100为拓扑标签；未运行LLMServingSim、完整现代DeepEP或完整RC操作，无硬件校准。不能把模拟完成时间称为真实模型推理时延。

新增来源目录由用户确认，原件仍按接收清单hash校验；远端修订未独立核实。八个请求固定首八步/layer3，BF16激活和专家1000ns是声明。16个路由输入的每连接总字节相同，offset只改变请求间时间对齐，不是真实新请求或已验证的调度优化。

程序职责：`analysis/prepare.py`读取本地来源；`analysis/setup.py`通过公开CLI生成物理网络并冻结预测；`analysis/run.py`逐条件调用公开持续字节探针，任何失败全局停止；`analysis/evaluate.py`只在评价端读实际结果；`analysis/close.py`独立核原件及六类篡改、生成报告；`tests/check.py`验证小型路由/工作量和手算链路估计。平台代码不依赖本实验。

首个检查器假定每包内部 `message_id` 存在。源码表明，无恢复记录时可为−1；包头完整。当前核验由实际IP/端口辨认数据和ACK，CNP按原生生成事件核对；没有给包增加标签或改变通信。冻结旧检查器、原失败和输入保持。端点包及普通ECMP投影对应的全端口字节一致，不能冒充每跳实际包捕获；精确队列积分来自原生全端口summary，未独立重建所有瞬时队列。

原件、二进制/库hash、实际命令与监控位于 `runs/route_alignment_20261027/`，准备监控位于相邻 `-preparation-monitor/`。本轮网络条件已关闭；以下只展示离线复核，不重跑网络：

```bash
PYTHONPATH=python:. python3 -B -m experiments.route_alignment.analysis.close \
  --stage runs/route_alignment_20261027 --output <不存在的新目录>
```

依赖冻结运行目录的该命令不会下载、构建或启动ns-3。搬迁核验入口见[恢复记录](results/20261027/recovery.json)，资源与阶段状态见[收尾](results/20261027/closeout.json)。没有测得对齐收益，也没有由此证明新论文贡献；完整研究目标仍进行中。
