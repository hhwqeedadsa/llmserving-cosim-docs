# 双端口数据传输筛选

本实验回答：保持当前token的远端目的地和总字节不变，改变token占用的通道序号，能否改善原生网络数据传输；改善是否超出普通链路服务量变化所能解释的范围。

这是全部数据在起点已就绪的持久字节微基准，**不是现代DeepEP或完整推理**。不模拟self GIN、控制通知、CUDA发起成本、GPU可见性或重排开销，也没有硬件校准。LPT只是经典强基线。方法、6个预定条件和预算见[protocol.md](protocol.md)，实际结论见[中文报告](../../docs/reviews/rail_channel_screening_20261023.md)。

在仓库根运行以下阶段，每阶段经公开`moe_ub_cosim.host_probe`限制60秒/RSS1GiB/地址2GiB/产物64MiB/卷预留8GiB；整轮480秒/192MiB。网络runner通过8条公开CLI生成两图并执行六次仿真，每次5ms/结果16MiB，不将六次耗时相加为一次应用执行。所有输出目录必须不存在、父目录存在；没有安装下载或自动提高预算。

```bash
python3 experiments/rail_channel_screening/study.py prepare \
  --repo /ABS/REPO --output /dev/shm/my-rail-study/input
python3 experiments/rail_channel_screening/study.py plan \
  --observed /dev/shm/my-rail-study/input/observed --output /dev/shm/my-rail-study/plans
python3 experiments/rail_channel_screening/study.py capture-time \
  --repo /ABS/REPO --output /dev/shm/my-rail-study/time-basis
python3 experiments/rail_channel_screening/run.py \
  --repo /ABS/REPO --observed /dev/shm/my-rail-study/input/observed \
  --plans /dev/shm/my-rail-study/plans --backend /ABS/NATIVE_STREAM_BINARY \
  --output /dev/shm/my-rail-study/network
python3 experiments/rail_channel_screening/analyze.py \
  --observed /dev/shm/my-rail-study/input/observed --plans /dev/shm/my-rail-study/plans \
  --network /dev/shm/my-rail-study/network --output /dev/shm/my-rail-study/analysis
python3 experiments/rail_channel_screening/check.py \
  --inputs /dev/shm/my-rail-study/input --plans /dev/shm/my-rail-study/plans \
  --network /dev/shm/my-rail-study/network --analysis /dev/shm/my-rail-study/analysis \
  --output /dev/shm/my-rail-study/verification.json
```

以上命令列出被监控子命令；外层监控例子为：

```bash
PYTHONPATH=python python3 -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/my-rail-study/plan-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 67108864 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/my-rail-study -- \
  python3 experiments/rail_channel_screening/study.py plan \
  --observed /dev/shm/my-rail-study/input/observed --output /dev/shm/my-rail-study/plans
```

准备需要本机四份原始JSON及`data/moe-trace-small/manifest.json`和固定来源缓存；缺原件时不能用随机数据代替。历史二进制SHA256是`136b22dc81e0b72d801575fe1d32f4f258a0c9648567c61400d02ddfbc218a4d`，runner默认校验此值。另行从固定源码构建时须显式传`--expected-backend-sha256`并记录新工具链/版本，它是一次新执行，不能冒充历史二进制；构建方式见[原生字节示例](../../examples/scaleout_rdma_streams/README.md)。

`package.py archive/recover/report --repo /ABS/REPO --root /ABS/THIS_RUN --output /ABS/REPORT_DIR`保存固定输入、源码、完整六份网络结果和日志；恢复在新源码根/cwd离线重新规划、分析和独立核验。**恢复不重跑网络，也不包含二进制及共享库**，不能当跨机网络执行复现。原始件和检查点仅易失内存盘，无持久备份；仓库保存小型HTML/JSON摘要。

独立checker核对四原件、逐warp/context、全体源包分段和每个数据/ACK包的完整物理端口路径，再独立累计端口服务时间。ACK按端点/plane/累计序号多重集及末端到达时间候选核对；现有公开捕获不含ACK的QP标识，不能声称逐QP ACK身份观测。端口服务时间按固定原生ns-3的DataRate及Time近邻纳秒取整；多个端口服务量不相加为完成时间。

## 逐包完成链残差核查

这项后续核查使用已验收的可选实际报文头，解决上一轮ACK连接身份歧义；上一轮归档与结果保持原样。[方法](residual_protocol.md)与[实际结果](../../docs/reviews/packet_residual_20261023.md)分别维护。只重采同样六个条件，去掉新增记录后完整结果字段须相等；不是独立新样本，也不是预测器。所需输入是上一轮完整工作目录或其恢复件，不能只给HTML摘要。

以下为受监控子命令，使用上述`host_probe`形式逐条限制60秒/1GiB/地址2GiB/64MiB，阶段产物根分别指定；全轮480秒/128MiB、六次网络总≤360秒，归档成员64MiB/压缩24MiB，卷预留8GiB。新目录的父目录先创建；各输出目录或输出JSON不得已存在。

```bash
python3 experiments/rail_channel_screening/residual.py prepare \
  --previous-root /ABS/PREVIOUS_RAIL_RUN --output /dev/shm/my-residual/input
python3 experiments/rail_channel_screening/residual.py capture-source \
  --repo /ABS/REPO --output /dev/shm/my-residual/time-basis
python3 experiments/rail_channel_screening/run.py \
  --repo /ABS/REPO --observed /dev/shm/my-residual/input/observed \
  --plans /dev/shm/my-residual/input/plans --backend /ABS/NEW_STREAM_BINARY \
  --expected-backend-sha256 NEW_BINARY_SHA256 --output /dev/shm/my-residual/network
python3 experiments/rail_channel_screening/residual.py analyze \
  --inputs /dev/shm/my-residual/input --network /dev/shm/my-residual/network \
  --output /dev/shm/my-residual/analysis-ties
python3 experiments/rail_channel_screening/residual_check.py \
  --inputs /dev/shm/my-residual/input --network /dev/shm/my-residual/network \
  --analysis /dev/shm/my-residual/analysis-ties --output /dev/shm/my-residual/verification-ties.json
```

本轮原生stream二进制SHA256为`aa531fbee822483b768265a0d32771ac31d5e1b45cd57846d11770b2db071608`，平台提交`9ee311b`。重新构建需显式记录自己的hash和工具链。`residual_package.py report/archive/recover --repo /ABS/REPO --root /ABS/THIS_RESIDUAL_RUN --output /ABS/REPORT_DIR`依次生成报告、核hash打包、换源码根与cwd离线重分析；恢复不执行ns-3、不需要旧二进制，不证明跨机网络复现。保留全部最大前驱和终态ACK并列项；所选链只是一条加性见证，传播项的最小/最大值遍历全部临界前驱计算，不能把其视为反事实不可改变成本。
