# 从消息额度到字节额度：网络与系统结构对照

本实验评估2026-10-22平台新增能力，不预设准入收益。只使用公开CLI，不导入平台测试或私有驱动。

固定8GPU、交错两组EP4、32请求、每rank batch4、H1024/E16/K2、1层2步ready-KV Decode、声明计算耗时、原合成路由及100G三层Clos。与上一研究保持输入，增加透传、消息数3、消息数3+字节8192的FIFO/轮转及最终ACK故障对照；路由和字节不为制造收益而改写。共同实际提交的任务图须逐字节相同。

竞争解释与否定条件：

1. 若网络排队主导，减小注入量应在减少排队的同时改善服务完成；若队列更小而服务更慢，就不能声称准入优化成功。
2. 若只限制消息数掩盖大小差异，count3的峰值逻辑字节应高于8192，新增额度应实际生效；否则该输入没有辨别力。
3. 轮转必须实际改变grant序列，才有讨论不同排序效果的依据；顺序相同即保留“本输入没有策略机会”的结果。
4. 最终ACK丢失时，源端确认不足、额度未回收与队列末态为空可以同时成立；不得把发送等待解释成持续网络排队。

另加一个**独立传输校验**：7个源向1个目标各发送128KiB，比较完整包记录与summary。它是明确的合成incast压力输入，检查拥塞标记/反馈、队列与观测模式；不是MoE专家热点分布、训练实测或优化收益样本。两个输入不拼接仿真耗时。

```bash
PYTHONPATH=python python experiments/scaleout_structure/run.py \
  --backend-json "$COSIM_BACKEND_JSON" --protoc "$COSIM_PROTOC" \
  --output /dev/shm/structure-experiment
python experiments/scaleout_structure/analyze.py \
  --input /dev/shm/structure-experiment --output /dev/shm/structure-analysis
```

运行限制沿用公开RDMA配置：单次60秒、RSS1GiB、地址2GiB、仿真5ms，累计产物256MiB、运行卷预留8GiB。失败保留，不自动提高预算或增加热点。分析须保留未完成段、失败条件和源payload中的重传，跨消息等待累计不是业务关键路径。
