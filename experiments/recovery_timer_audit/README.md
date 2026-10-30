# 恢复计时语义与发送调度机会

本轮核查上一轮两个推论是否成立：部分ACK推迟尾段重试是否足以称为漏洞；MoE恢复调度是否确有等待发送的多个候选。沿用`541fce5650`的128端点Clos真实路由结果，不运行新网络、不改协议、不估算反事实完成时间。

已知预检结果：RFC6298第5.3节明确重启新ACK计时；两个prefix条件的10次超时首个重传均同纳秒发出。本轮不是盲测，进一步检查完整缺口重发批次、SACK/快速重传、源NIC实际占线和来源边界。没有按结果调整故障、负载或计时参数。

## 输入与方法

- 从上一轮已验收analysis/receipt核对normal、local-w20、prefix-w20、prefix-w60原始结果hash；源序号、真实反馈和实际TX均从公开结果读取，不导入旧实验驱动或fixture，不读取目的端真值来决定源端缺口。
- 源发送按累计high水位独立识别重复字节；恢复触发包括真实timeout和源端fast_retransmit。用触发前实际源ACK/SACK重建`[ack,high)`缺口，处理同纳秒反馈顺序歧义。批次只跟踪下个源反馈/恢复决定前的真实重发；没有发齐则保留censored，不假设补发成功。
- 逐次报告首个重传延迟、缺口批次覆盖、末包启动/串行化结束、同NIC其他QP实际占线、同源等待区间重叠。只有正长度区间才算发送等待。传输正在链路上或在等ACK，不等同于待发送。
- 这能筛查“只对已经到期的恢复发送重新排优先级”在原轨迹的施力位置，不能证明所有调度没有价值，也不能从原轨迹推导新策略的完成时间或可消除上限。
- RFC6298和RFC8985均是TCP资料，不是RoCE规范；Linux v6.12 RXE是软件RoCE实现，不是本机mlx5固件。检验重启、QP超时离散编码、PSN和ACK请求的区别，拒绝将17µs、每包ACK或项目内MSR1扩展写成通用硬件事实。

## 预算与复核

根`runs/recovery_timer_audit_20261030`，300受监控秒/64MiB/留8GiB，每阶段60秒/RSS512MiB/地址1GiB。零新网络、构建、安装或依赖变更。预检公开资料共五请求，声明超时上界合计53秒，成功四项；raw GitHub请求超时，改用不同的官方API来源，未重试同一失败请求。合计正文限2MiB、单响应200,000B，来源固定及hash核验后使用；无大型下载。sysfs设备与失败verbs查询只说明可见状态和当前访问，不作硬件标定。

```bash
.venv/frontend/bin/python -B experiments/recovery_timer_audit/monitor.py --root /new-root --stage prepare -- \
  .venv/frontend/bin/python -B experiments/recovery_timer_audit/audit.py prepare \
  --previous experiments/explicit_prefix_moe/results/20261030 --acquired /directory/of/public-responses --output /new-root/input
.venv/frontend/bin/python -B experiments/recovery_timer_audit/monitor.py --root /new-root --stage analyze -- \
  .venv/frontend/bin/python -B experiments/recovery_timer_audit/audit.py analyze --input /new-root/input --output /new-root/analysis.json
```

`sources.py --help`说明公共响应获取或本轮缓存导入；`verify.py`独立用字节集合与实际源物理TX检查缺口和批次。`publish.py`生成中文HTML与数据表。报告与平台功能分开提交；失败保留，不提高预算或自动重跑成功网络。
