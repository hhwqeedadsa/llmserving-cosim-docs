# 极端故障压力测试

本轮按用户要求构造6种极端条件，固定场景后执行，保持DCQCN、选择性重传和已有本地恢复机制。它是人工合成MoE风格的通信依赖微基准，没有真实专家路由分布、现代EP库执行、GPU数值计算或硬件校准。事前要求、资源额度和准备阶段更正见[protocol.md](protocol.md)。

## 结果与边界

[网页](results/20261025/report.html)、[数值摘要](results/20261025/summary.json)和[独立核验](results/20261025/verification.json)列出每个条件的状态。

| 条件 | 任务完成时间 | 包捕获 |
|---|---:|---|
| 均匀负载，正常网络 | 2.566232ms | 99,286条，完整保存 |
| 87.5%热点，正常网络 | 4.223689ms | 95,898条，完整保存 |
| 同一热点，一条上联失效 | 4.883365ms | 100,000条，遗漏12,070条 |
| 核心失联 / 预存核心失联 / 热点ToR全断网 | 已构造，未运行 | 按规则停止，不追加 |

故障相对同一热点正常网络增加659.676us、变慢15.62%；热点相对均匀增加64.59%。热点加故障相对均匀共变慢90.29%，不能把这个总变化全归给故障。恢复到当前热点正常时长对应13.51%的参考缩短，未证明其可消除或为严格上界，未测新方法收益。

三次任务均完成90工作、64写和4,194,304B唯一载荷。独立核验按合成token路由重新累计字节，核对全部依赖/实际完成、源记录覆盖和连接ACK前缀；没有声称所有物理跳与ACK逐包重建。第三个条件达到事前捕获上限，关闭本阶段并保留完整源记录与已捕获前缀。剩余三个场景无结果，分区失败也未实际验证。

## 配置与运行

[config.json](config.json)声明token数、hidden维度、人工热点分布、带宽、计算占位和六个故障日程。调整配置时需保持每源token分配总和一致，另立新协议/预算；当前生成器为固定8端点、两步、Top1压力测试，不是任意模型自动适配器。

以下仅说明怎样在**新的独立阶段**执行；本轮目录已经关闭，不可追加或重跑。`STRESS_STAGE`须为新目录，`STRESS_BACKEND`为与锁定依赖一致的既有stream-probe；不安装新依赖或构建程序。

```bash
export PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1
python3 -B experiments/extreme_fault_stress/prepare.py \
  --repository . --config experiments/extreme_fault_stress/config.json \
  --output "$STRESS_STAGE/inputs"
python3 -B experiments/extreme_fault_stress/run.py \
  --repository . --stage "$STRESS_STAGE" --backend "$STRESS_BACKEND"
```

实际阶段使用`/dev/shm/moe-extreme-fault-stress-20261025/inputs-v3`。`commands.json`记录公开CLI和逐项资源监控；`runtime.json`核既有二进制和八库，固定原生`68a07f7`。两次配置格式拒绝和一次驱动空目录已存在错误都发生在native开始前，原输入和日志保留。另有只读诊断把整数误当可迭代对象的错误，不改变输入或网络。

实际分析/复核入口：

```bash
python3 -B experiments/extreme_fault_stress/analyze.py \
  --stage "$STRESS_STAGE" --inputs "$STRESS_STAGE/inputs-v3" --output "$STRESS_STAGE/summary.json"
python3 -B experiments/extreme_fault_stress/verify.py \
  --stage "$STRESS_STAGE" --inputs "$STRESS_STAGE/inputs-v3" \
  --analysis "$STRESS_STAGE/summary.json" --output "$STRESS_STAGE/verification.json"
python3 -B experiments/extreme_fault_stress/render.py \
  --stage "$STRESS_STAGE" --output experiments/extreme_fault_stress/results/20261025
```

持久证据包由`checkpoint.py`生成，位置为`runs/extreme_fault_stress_checkpoint_20261025/evidence.tar.gz`。恢复时先检查成员类型、路径和hash，再在新研究源码根、空cwd离线复算摘要与核验，不执行网络。归档不含native运行时，不宣称从零构建或异机硬件复现。
