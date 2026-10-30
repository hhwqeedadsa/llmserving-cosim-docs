# 真实专家选择下的短时共享上行故障

研究问题：链路会重新上线时，本地剔除坏出口相对延迟全局改路能减少多少任务等待？较少重传是否真的更快？成熟的即时全局改路参考是否已经消除主要空间？本轮不是新协议或生产收益验证。

## 运行前固定的方法

- 128端点Clos、20交换机、四个交错EP32组。15份不同DeepSeek-R1-AWQ/MMLU真实路由原件各一请求，组内请求数4/4/4/3；层3/4、两个单token Decode步、每源rank的batch=1。每组256专家分布于32个rank，每rank8专家。保持已有逐贡献消息图、原ACK/集合门限和字节缓冲释放条件，不改为现代DeepEP完整执行。
- 复用公开准备入口独立生成输入。计算是声明成本，数据是功能标记；网络由同一个固定原生RDMA ns-3运行推进。不是完整671B模型、实测GPU、Prefill、大batch或128卡满载。
- 沿用SR选择性重传：初始17µs、上限640µs、3次重试及参考DCQCN；禁用备用，不使用弱80µs唯一基线。
- 第一条ToR–Agg边128–132在4µs双向关闭，分别在24/64µs恢复，即中断20/60µs。位置沿用此前按拓扑排序确定的边，不按新结果寻找热点。
- 每个持续时间比较`global`：关闭和恢复通知各延迟60µs；`local`：相同通知延迟，额外立即从两端旧ECMP候选剔除坏出口，物理上线时重新筛选；`instant`：两种通知延迟均为0，无本地策略。没有额外控制报文；local/instant均为零检测编程开销的理想参考，instant不是时延数学下界或可部署性能。
- 共七个新网络：一个无注入`normal`，以及`global-w20/local-w20/instant-w20/global-w60/local-w60/instant-w60`，每个仅运行一次。新normal与已验收十五请求17µs正常原件比较完整结果，只允许同hash库的路径变化；新网络均使用临时恢复平台构建。已知旧永久故障及DATA黑洞结果，不宣称盲测。
- `w20/w60`只表示物理故障窗口；`global/local`的60µs表示通知延迟。w60的上线与关闭通知在64µs同刻发生，独立包/控制记录不能推出跨数组同ns顺序，核查保留歧义。

## 判别与收益口径

1. 若local减少坏出口损失但使其他共享出口排队，则重传减少不一定带来任务提前；逐组报告而不只挑最快组。共享边影响多个组，不把某组预先称为“健康组”。
2. w20中链路24µs已上线、关闭通知64µs才到、恢复通知84µs到；控制面64–84µs仍按已知关闭状态排除。它是显式通知模型，不能读取物理真值跳过；检查真实状态、通知版本和实际重新使用原边。
3. 比较同窗口global→local的最晚任务减少量、相对global完成时间的比例、相对global较normal额外等待的收回比例。残余normal差距及instant差距只作参考，不称可消除上限，不作事后最快策略。
4. 报告四组完成、网络排空、有效/重传payload、DATA/ACK/NACK物理及本地丢弃、buffer和队列峰值。核对原始路由/图/输入相同、源字节和真实ACK、每连接QP持续、完整捕获与物理排空；异常先查实际末前驱及包路径，不把累计驻留直接当业务收益。

## 预算、执行与复算

根`runs/transient_uplink_recovery_20261030`；累计1800受监控秒、新增2GiB、卷留8GiB，单阶段120秒/RSS1GiB/地址2GiB。native保持60秒/模拟5ms、包/队列200000行、单运行产物512MiB/结果256MiB。最多七新128Clos网络；无构建、安装、下载、依赖或平台行为改动、GPU、成功网络重跑，失败保留不自动抬限。

先提交本方法及执行/核验代码，再运行准备；输入、源码、构建来源、程序、库和旧正常原件hash写入frozen后才能运行。仅使用公开prepare和`scaleout rdma-run`，不依赖fixture或旧实验驱动。运行目录必须不存在；真实路由原件不提交Git。

```bash
export PYTHONPATH=python
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/monitor.py --root /path/to/new-root --stage prepare -- \
  .venv/frontend/bin/python -B experiments/transient_uplink_recovery/study.py prepare \
  --source-manifest /path/to/manifest.json --source-base /path/to/repo \
  --build /path/to/verified-restore-build --normal /path/to/retained-normal --output /path/to/new-root/study
.venv/frontend/bin/python -B experiments/transient_uplink_recovery/monitor.py --root /path/to/new-root --stage normal -- \
  .venv/frontend/bin/python -B experiments/transient_uplink_recovery/study.py run \
  --study /path/to/new-root/study --case normal --protoc /path/to/protoc
```

其余case同样执行；随后用`study.py analyze --study ... --output /new-analysis-dir`和独立`verify.py --study ... --analysis .../analysis.json --output /new-verification.json`。展示另行生成，不参与在线控制。平台依据见[恢复示例](../../examples/scaleout_link_restore/README.md)和[功能验收](../../fixtures/temporary_link_restore/README.md)。
