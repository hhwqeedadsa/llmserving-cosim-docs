# 真实请求增多与局部故障持续时间下的SR基线

上一轮四请求实验中，17µs初始重传超时收回了原80µs超时下92.87%的故障额外等待。这不能证明17µs在其他负载或故障窗口下同样有效。本轮扩展输入覆盖，检查成熟超时/指数退避本身的收益与代价；不提出新协议。

固定128端点三层Clos、四组EP32、R1/H7168/E256/K8、原层3/4、两个Decode步；15份不同的本地R1-AWQ/MMLU路由原件各使用一次，rank0..14各一个请求，四组4/4/4/3请求。真实的是专家选择；计算为声明值，载荷为标记字节，不是完整671B、物理GPU测量或128卡满载。四请求与十五请求的绝对时延不能归因于单一网络参数；主要结论来自十五请求内部匹配对照。

## 执行前固定的方法

先由公开`examples/scaleout_message_buffers/prepare.py --route-files 15`生成并校验输入。全部条件使用`observe_only/1`，不发起任何备用；保留原Dispatch/Combine门限、源累计ACK、实际字节缓冲及排空。所有流/图、成本、连接、最大RTO640µs、3次重试和参考DCQCN不变，只有初始RTO或故障窗口变化。

固定三个策略：`base`为80µs；`transfer`为上一轮得到的17µs，直接跨负载沿用；`calibrated`先且仅运行一次十五请求的`base-normal`，从全部实际消息ready→ACK跨度最大值取下一个整微秒。它包含排队，不是RTT估计、最优RTO、独立验证集或生产配置建议。若两个策略RTO相同，冻结显式别名映射，同一配置只执行一次，不制造重复样本。

固定四种条件：`normal`不注入故障；`loss20`/`loss60`/`loss120`分别在[0,20/60/120µs)丢弃到GPU32的所有DATA（含重传），保留ACK/控制包。GPU32沿用上一轮受损位置，虽然输入生成器对新负载可选出另一个热点，这里不重新选择更不利位置。故障时长覆盖不同退避轮次，是机制压力条件，不是生产故障概率分布。校准完成并冻结后才运行其他条件；不按故障结果调超时、窗口、位置、负载或重试数。

竞争解释与判别：

1. 如果17µs仅适合四请求负载，新正常条件可能出现误重传；比较原包/实际源重传、正常完成时间和校准策略，而非只看目标故障收益。
2. 如果指数退避跨过故障窗口，较短初始RTO也可能更晚恢复。逐包检查超时、故障窗口内无效重传、窗口外首个有效到达、源ACK和Dispatch门限；不靠终态差推断机制。
3. 原门限可能放大少数消息迟到的影响。按相同图检查受损/未受损专家的输入、实际计算开始与FIFO等待，不删除门限制造收益。
4. 提前恢复可能增加与其他组的重叠。逐组报告全部请求片段完成、额外载荷、缓冲峰值和物理排空；无干扰、负收益或失败也正常报告，不以发现新现象为验收条件。

最多12次新网络，各唯一配置只运行一次。每次保留公开命令、退出/失败、输入/源码/构建及库哈希；公开`rdma-check`和`contribution-frontier`复核完成记录，独立核算全部消息和源包字节及门限。原始路由、大包及构建留本地，发表派生摘要和复现脚本。

独立预算：`runs/transport_window_coverage_20261030`累计2400受监控秒、新增2GiB、卷留8GiB；CPU/CLI各120秒/RSS1GiB/地址2GiB；native60秒/模拟5ms/全包200000条/产物512MiB/结果256MiB。零构建、下载、依赖修改和GPU；失败保留，不自动提高限额或重跑成功网络。平台准备能力与研究实验分别验收提交。

## 复现

在仓库根、已有前端环境设置`PYTHONPATH=python`。所有输出路径必须不存在，命令以`monitor.py --root /path/to/task --stage UNIQUE -- ...`包裹。

```bash
python experiments/transport_window_coverage/study.py prepare \
  --source-manifest /path/to/fifteen-source-manifest.json --source-base /path/to/base \
  --build /path/to/public-build --output /path/to/new-study
python experiments/transport_window_coverage/study.py run --study /path/to/new-study --case base-normal --protoc /path/to/protoc
python experiments/transport_window_coverage/study.py calibrate --study /path/to/new-study
python experiments/transport_window_coverage/study.py run --study /path/to/new-study --case transfer-normal --protoc /path/to/protoc
```

按冻结映射再运行其余唯一case，随后`study.py analyze --study /path/to/new-study`和`verify.py --study /path/to/new-study --output /path/to/new-verification.json`。别名case不会再次发起网络，收据会明确记录复用。
