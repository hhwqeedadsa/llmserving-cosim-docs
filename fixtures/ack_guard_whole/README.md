# 源确认超时触发完整备用：平台验收

新格式`moe-whole-call-replication/5`在实际源SEND post后计时，以原生控制定时器轮询已观察的源完成前缀，超过期限才提交完整多步备用，保留原绝对整段兜底。原生watchdog通知与本功能仍隔离；轮询可能晚于消息自身期限，不冒充逐消息精确定时。

[CPU与旧网络验收记录](pre-network.json)记录77项不同CPU测试、公开示例准备及四份旧网络的公开只读检查。首次扩展回归命令引用不存在的`service_wakeup_test`，保留失败；更正测试命令后77项通过，没有修改产品代码绕过错误。当前验收没有新增网络、编译或依赖修改。

CPU检查覆盖实际post起算、动态检查时间、无pending时继续保护、确认先于检查、同纳秒前缀顺序、整段兜底、一次启动、非法消息/顺序和预算拒绝，以及真实LLMServingSim调度器的两个Decode步与KV实际释放。小型CPU人工输入不充当128端点研究结果。

仓库根目录、已安装前端环境下可复核：

```bash
PYTHONPATH=python:tests/unit OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/frontend/bin/python -B -m unittest guarded_whole_test sequential_whole_test \
  multicall_deadline_test delayed_whole_test whole_call_test service_terminal_test \
  execution_events_test source_ack_watchdog_test
```

公开输入准备见[共享网络示例](../../examples/scaleout_multicall/README.md)，配置与报告语义见[完整备用说明](../../docs/whole_call.md)。真实路由研究独立验收；平台通过不代表任何新算法或性能收益。

四份新128端点Clos网络的公开运行、全图/源确认/原生定时/KV与独立前缀验收见[网络收据入口](post-network.json)及[研究结果](../../experiments/ack_guard_whole/results/20261030/README.md)。目标收益与成本属于该单样本对照，不作为平台测试通过条件。
