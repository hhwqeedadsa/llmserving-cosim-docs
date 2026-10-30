# 原始Dispatch恢复的成熟基线

研究问题：上一轮单贡献备用没有推进完整集合就绪，直接缩短原始传输的恢复等待是否更有效？本轮比较既有选择性重传的80µs初始超时和正常样本校准的超时，不提出新协议。

使用128端点Clos、四组EP32、四份不同R1-AWQ/MMLU专家选择、原层3/4和两个Decode步。计算为声明值、载荷为标记字节；不是完整671B、硬件校准或全卡满载。保留全部原图门限、源确认、参考DCQCN与缓冲退出。四条件的备用策略均为`observe_only/1`，实际不启动备用。

在执行新网络前固定方法：

1. 通过公开消息缓冲示例重新准备真实输入，保持上一轮部署及故障选择规则。normal不额外注入故障；local在前20µs丢弃发往首组首层Dispatch字节量最大目标卡的DATA，保留控制包。由输入选位置，不按新结果调故障。
2. 先且仅执行一次`base-normal`。对全部实际消息计算`ack_ns - ready_ns`，取最大值的下一个整微秒为短超时；这是包含发布/排队的保守消息跨度，不是RTT估计、最优RTO或独立泛化验证。若不小于80µs，仍如实执行和报告，不另调参。
3. 校准清单冻结后，`base-local`、`short-normal`、`short-local`各执行一次；仅初始RTO随策略改变，最大RTO640µs、3次重试及其他成本不变。正常重传和多余字节必须报告。短超时可能在故障尚未恢复时多重传，这是待检查的代价。
4. 竞争解释：若原始Dispatch确认阻塞主导，较短RTO应推进Dispatch门限、专家实际开始及目标完成；若只推进单结果或被其他依赖抵消，不应有完整收益。正常任务、其他三组、字节、缓冲峰值、完整排空分别核对。不把阶段差与请求差相加，不把正常/故障差距写成理论可消除上限。

最多四个新网络，零构建、依赖变更、下载和真实GPU；累计1200受监控秒/新增1GiB/卷留8GiB。每CPU或CLI阶段120秒/RSS1GiB/每进程地址2GiB；native60秒/模拟5ms/完整包200000条/每次产物512MiB、结果256MiB。失败保留，不重跑成功网络或自动扩限。复用公开构建的二进制和8个ns-3库，并核验编译来源及当前源码。方法先提交，再准备和运行；旧故障已有认知，不称盲测。

复现入口（仓库根，设置`PYTHONPATH=python`并使用已有前端环境；外层须按上述预算监控）：

```bash
python experiments/transport_baseline/study.py prepare \
  --source-manifest /path/to/manifest.json --source-base /path/to/base \
  --build /path/to/public-build --output /path/to/new-study
python experiments/transport_baseline/study.py run --study /path/to/new-study --case base-normal --protoc /path/to/protoc
python experiments/transport_baseline/study.py calibrate --study /path/to/new-study
python experiments/transport_baseline/study.py run --study /path/to/new-study --case base-local --protoc /path/to/protoc
python experiments/transport_baseline/study.py run --study /path/to/new-study --case short-normal --protoc /path/to/protoc
python experiments/transport_baseline/study.py run --study /path/to/new-study --case short-local --protoc /path/to/protoc
python experiments/transport_baseline/study.py analyze --study /path/to/new-study
```

`study.py`只调用公开准备/执行/核验/贡献诊断命令，分析读取保存的公开输出，不导入平台内部或测试代码。结果生成`delivery/analysis.json`、HTML和PNG/SVG。源路由与大产物留在本地，不随研究摘要发布。
