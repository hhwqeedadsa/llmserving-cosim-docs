# 网络故障实验审阅材料

- [HTML阅读版](report.html)：四组实际对照及逐包可展开时间线。
- [结果与边界](../../../experiments/scaleout_faults/results.md)、[复现方法](../../../experiments/scaleout_faults/README.md)。
- [summary.json](summary.json)：固定工作量、目标消息、实际回调、节点及请求状态。
- [timeline.json](timeline.json)：目标消息的设备发送/接收/丢弃与BEgressQueue入/出队，属于内部评价资料。

页面由`experiments/scaleout_faults/render.py`从实际保存产物生成，不调用模型或外部网络。报告不把预期故障失败算作仿真成功，不宣称完整硬件校准、千卡故障行为或新策略收益。
