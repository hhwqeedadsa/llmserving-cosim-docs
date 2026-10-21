# Scale-Out软件准入与故障结果

- [HTML阅读版](report.html)：7组固定输入对照，含负面结果与适用边界。
- [完整分析说明](../../../experiments/scaleout_admission/results.md)：软件等待、网络排队与业务停滞的证据及后续判断。
- [summary.json](summary.json)：各组实际完成、请求末态、资源及队列指标，含失败观察终点和全部终态所有者。
- [target-timeline.json](target-timeline.json)：目标包事件、源GPU生命周期、故障规则、图进展与前端波次。
- [来源与核验](../../../experiments/scaleout_admission/evidence.json)：平台提交、脚本及输出摘要。

重复分析逐字节一致，HTML表格行数和本地链接已检查；没有浏览器视觉验收。数据为8卡、固定声明成本、合成路由、ready-KV Decode，不能替代硬件容量/恢复校准或生产p99结果。FIFO与轮转实际准入序列相同，不能从本例宣称策略性能差异。原始归档在本机`runs/scaleout_admission_experiment_20261021/`。
