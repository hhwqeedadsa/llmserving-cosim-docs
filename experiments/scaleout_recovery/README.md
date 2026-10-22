# 用固定 MoE 任务验证仿真器可靠性

目标是让网络故障后的业务推进和失败解释可信，不比较新的调度策略收益。固定8 GPU、两个交错 EP4 副本、每 rank batch4、H1024/E16/Top2、1层2步 ready-KV Decode、100G 三层 Clos；计算与40 μs初始超时均为声明值。原合成专家选择不变，不把它称为真实热点或训练输入。

沿用8192 B/3消息的每源FIFO软件额度，故障目标从首个实际图中选取0→4 Dispatch，不预设图节点完成时间。五个条件为原生无故障、恢复无故障、原生最终ACK丢失一次、恢复同一ACK丢失一次、持续丢失至重试耗尽。共同提交的图须逐字节相同，全部组与步骤共享一个网络实例。

待检验的解释：

1. 若原停滞来自最终确认缺失，接收数据可已齐、源确认不足、交换机队列为空；原生运行仍不能成功，不能解释成队列一直堵塞。
2. 恢复必须通过实际重传与反向ACK，使源确认补齐、QP清理、额度释放，随后真实前端继续；直接填完成时刻不算验证。
3. 无故障恢复不应改变当前声明超时范围内的执行。持续丢失不能被“恢复功能”包装成成功，必须在有限时间失败并保留状态。

```bash
PYTHONPATH=python python experiments/scaleout_recovery/run.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/recovery-experiment
python experiments/scaleout_recovery/analyze.py \
  --input /dev/shm/recovery-experiment --output /dev/shm/recovery-analysis
```

使用公开CLI，不导入测试驱动或历史结果。配置在本目录，输入快照、来源hash、命令、预期非零退出、结果和资源记录在输出目录。单次宿主60秒/RSS1GiB/地址2GiB/仿真5ms，累计产物256MiB、卷预留8GiB；失败不自动扩限或重试。分析为离线评价，可读取全局时序；在线恢复只读取源发送/ACK和接收端本地重复包状态。

结果见[results.md](results.md)，平台功能覆盖另见[恢复验收](../../fixtures/scaleout_recovery/README.md)。未跑千卡、未增加真实路由或硬件标定，不把相对于永不完成的原生失败转为完成叫作网络性能加速。
