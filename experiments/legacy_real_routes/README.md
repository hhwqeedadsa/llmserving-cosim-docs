# 真实专家选择下完整旧库通信的剩余等待

本轮检查：保留旧DeepEP v1.2.1逐专家通知和实际发送发布后，真实专家选择驱动的完整通信调用在成熟恢复之上还剩多少等待？不把选择性重传、ECMP过滤或显式绕路作为创新。

在运行前固定以下方法，输入与源码必须先提交并冻结，三个条件各运行一次，不按新结果改变故障、路由选择或RTO。

- 网络固定128端点Clos、20交换机、168链路；一个EP32组，rank映射到端点0/4/…/124。其余96端点没有该组工作，不是128卡满载或多组服务。
- 来源为本机已确认的15份不同R1-AWQ/MMLU原件，全量hash/结构校验后，每份取layer3、entry1的单个Decode token。按清单顺序分配到逻辑rank0..14，均在本次调用开始可用；源rank和同时到达属于实验设定。保留全部真实Top8选择，不复制请求、不按热点挑文件。未知远端版本、激活精度和采集硬件保留，BF16为显式功能载体，不是已测AWQ激活。
- 一个完整Dispatch/Combine调用，E256、H7168、每rank8专家，零token通知保留。旧库功能模型、零成本可见性/专家拷贝，不经过LLMServingSim请求调度，不执行671B数值模型或GPU。此前人工输入功能验收不作为本轮真实来源证据。
- 同一15份输入比较normal、local、prefix。后两者将按拓扑固定的128–132边在4µs关闭，24µs恢复；两种全局通知均延迟60µs。local从旧候选剔除坏出口，prefix在无可用候选时执行已实现的显式修复段。检测/编程成本仍为零的参考假设，不是可部署设备性能。正常条件无注入。
- SR初始17µs、上限640µs、最多3次重试、inflight4096B/接收8192B，参考DCQCN日志100,000条；payload1024B、chunk4096B。相同拓扑、消息图、源输入、队列深度32和所有完成/缓冲条件。prefix真实封装开销保留，不改变ACK语义。

竞争解释：若故障主要阻塞ACK或零token通知，数据到齐不应等同于调用完成；若其他前驱掩盖恢复，本地重传时间不会等比例体现为任务损失；若成熟绕路消除主要损失，新调度的收益空间应收敛。报告通信调用、物理排空、重复载体、参考事件及逐rank完成，必要时沿真实前驱继续诊断。prefix与normal差仅作参考，不称可消除上限。15个单token的频率是本窗口访问分布，不证明长期或训练热点。

预算根`runs/legacy_route_study_20261031`：累计900受监控秒、1GiB新增产物、卷留8GiB，阶段120秒/RSS2GiB/地址4GiB；每网络120秒/仿真5ms/结果256MiB，源端捕获最多100万行。最多三个新网络；失败保留，不自动重跑或提高预算，不安装、下载、构建、改依赖或使用GPU。公开审计迁移的只读检查也计入该根。

在仓库根设置`PYTHONPATH=python`，所有阶段须经上述预算的`moe_ub_cosim.host_probe`监控。先用`study.py prepare --source-manifest ... --build ... --output ...`准备并冻结，再分别`study.py run --study ... --case normal|local|prefix`，最后`study.py analyze --study ... --output ...`。只使用公开平台命令/稳定读取接口，不依赖测试驱动。代码中的默认路径不承担来源授权或自动下载。

## 结果和复核入口

以上为事前方法；执行后结论单独保存在[RESULTS.md](RESULTS.md)，网页见[本轮结果](results/20261031/report.html)。`results/20261031/source-manifest.json`保存可迁移的文件路径、原下载路径及hash，原始路由JSON不提交。复现者需自行取得对应数据集目录中的15份匹配原件，按清单放入仓库`data/moe-trace-small/`；远端版本未知，不自动下载或承诺当前远端内容仍匹配。来源清单的路径改为仓库相对路径，与本轮原清单具有不同的清单hash，文件hash保持；它用于新运行，不能替换旧运行的冻结原清单。

新网络复现需要先按[公开旧库示例](../../examples/legacy_native/README.md)准备匹配的固定后端及构建身份，不能只凭一张图复现网络。设置新的预算根，逐阶段使用`PYTHONPATH=python python3 -B -m moe_ub_cosim.host_probe`，配置本方法的时间、内存、磁盘和卷余量限制，然后依次调用：

```bash
python3 -B experiments/legacy_real_routes/study.py prepare \
  --source-manifest experiments/legacy_real_routes/results/20261031/source-manifest.json \
  --build /path/to/compatible-build/backend.json --output /path/to/new-study
python3 -B experiments/legacy_real_routes/study.py run --study /path/to/new-study --case normal
python3 -B experiments/legacy_real_routes/study.py run --study /path/to/new-study --case local
python3 -B experiments/legacy_real_routes/study.py run --study /path/to/new-study --case prefix
python3 -B experiments/legacy_real_routes/study.py analyze \
  --study /path/to/new-study --output /path/to/new-analysis
```

上面列出的是监控器后的子命令，不是绕过监控的执行建议。`prepare`会绑定当次源码、构建、数据和参数；新检出应先保存本地方法提交，再准备。当前`study.py`已修正local配置字段，新运行不需重演旧错误；本轮历史原错误、修订冻结和已完成normal原件保留。

后处理不启动网络：`publish.py --study ... --analysis .../analysis.json --output NEW --publish-dir FINAL`生成三条CSV、两张PNG和HTML，`--publish-dir`仅计算最终页面的相对方法链接，并不执行复制。最终目录需同时放入`analysis.json`、核验生成的`verification.json`，然后检查页面相对链接。

保留本机原件时，使用新输出目录进行独立核验：

```bash
python3 -B experiments/legacy_real_routes/verify.py \
  --root runs/legacy_route_study_20261031 \
  --delivery runs/legacy_route_study_20261031/delivery-v3 \
  --output /path/to/new-verification
```

该命令同样放在上述`host_probe`之后；它专门复核本轮冻结及失败修订，必须保留本轮原来源清单和监控日志，不是任意新网络的通用验收器。公共完整通信审计使用`scaleout deepep-audit`。所有核验禁止Python `-O`；源码hash变化时不能通过改旧摘要绕过，应保留对应提交及原件。
