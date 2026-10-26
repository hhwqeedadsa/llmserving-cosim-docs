# 现代完整调用的选择性重传接入

对应[规范](../../docs/tasks/modern_selective_bridge_spec.md)和[公开示例](../../examples/modern_deepep_native/README.md)。补齐 `provider.dcqcn_reference` 到现有v5事务配置的透传；原生后端、现代操作图和缺省输入行为保持。选择重传和Figure7参考控制器是既有基线，不是新恢复算法。

2026-10-26的四次网络全部完成，阶段关闭，不追加第五次：

| 输入 | 正常完成 | 指定首请求包丢失后完成 | 实际丢包 | 现代事件 |
|---|---:|---:|---:|---:|
| 公开双rank、两次调用 | 105,621ns | 123,921ns | 1 | 322 |
| 原四路由、EP4、H7168、E256、Top8、单次调用 | 70,610ns | 77,160ns | 1 | 566 |

两输入计算成本和工作量不同，不能横向比较速度。四路由故障相对正常增加6,550ns；这是一个指定丢包条件的模型结果，不是故障平均、生产p99或新方法收益。故障条件分别出现2/10次重复源包，目标副作用仍恰好一次；可靠性不代表绝不重复发送。

独立核对源字节/帧、目标内存及原子旧值、源保护释放、信号/屏障、所有现代操作依赖和原生回调批次；核对MSR1选择反馈的实际编码及生成/接收对应、ACK完整性和故障实际命中。四卡正常/故障分别遗漏103,168/103,282条物理捕获，不能宣称完整逐跳还原。两份原始缺省输入的初始事务、现代计划和连接与 `aeaf4aa` 完整相同，未重跑历史正常网络。

41项输入/事务/流单元检查和21项受影响回归通过，8类证据篡改被拒绝。一次新增离线检查误要求空响应流具有恢复定时器，已按后端“首次发布数据才建立定时器”的语义修正并保留失败；没有重跑成功网络或更改平台来迁就检查器。

原件 `/dev/shm/moe-modern-selective-20261026/`，持久包 `runs/modern_selective_checkpoint_20261026/checkpoint.tar.gz`。Git保留[验收](verification.json)、[新根复核](recovery.json)和[预算收尾](closeout.json)。包含实际四组输入、结果、源码及同主机ABI所需的程序/八库。可用版本化入口恢复并离线复核，不再执行网络：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.modern_selective.checkpoint restore \
  --archive runs/modern_selective_checkpoint_20261026/checkpoint.tar.gz \
  --output /dev/shm/modern-selective-review
cd /dev/shm/modern-selective-review/empty-cwd
PYTHONPATH=/dev/shm/modern-selective-review/source/python:/dev/shm/modern-selective-review/source \
  PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.modern_selective.recover \
  --restored /dev/shm/modern-selective-review --output /dev/shm/modern-selective-review/review.json
```

这是合成标记字节与声明计算成本的功能验证，不是完整LLM服务调度、数值专家执行、GPU/provider校准、干净原生构建或完整千卡验证。四份输入来自用户下载并确认目录的DeepSeek-R1/MMLU专家选择记录；只说明来源，不推导生产代表性。
