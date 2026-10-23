# 丢包恢复成本与优化空间

本轮固定任务、拓扑、带宽和源资源，实际比较中间数据包、尾包和最终ACK丢失，以及40/20/12μs初始超时。原始问题和13条件预登记见[protocol.md](protocol.md)，结果见[审阅HTML](../../docs/review_artifacts/loss_recovery_cost_20261023/report.html)。RTO表示重传超时时间；这里是参数对照，不是新算法或硬件配置建议。

`service.json`为8GPU/两个交错EP4/每rank batch4/H1024/E16/Top2/1层2步的有限Decode，计算为声明值。`topology.json`为100G三层Clos，`protocol.json`保留每源3消息/8192B FIFO、原生DCQCN与相同被动观测。它们由先前研究的输入复制，执行不导入旧研究程序或测试。

在有平台Python依赖的既有环境中，从仓库根执行：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python -B experiments/loss_recovery_cost/run.py \
  --backend "$COSIM_NATIVE_SERVICE" \
  --runtime-manifest "$COSIM_BUILD_VERIFICATION" \
  --library-manifest "$COSIM_RUNTIME_LIBRARY_HASHES" \
  --protoc "$COSIM_PROTOC" --output /dev/shm/NEW_LOSS_COST
python3 -B experiments/loss_recovery_cost/analyze.py \
  --input /dev/shm/NEW_LOSS_COST --output /dev/shm/NEW_LOSS_ANALYSIS
```

`--runtime-manifest`读取已有构建记录中的`binaries.ns3.42-moe-rdma-service.sha256`和`source_hashes`，`--library-manifest`读取八个`.so`绝对路径到SHA256的映射；运行前后核对身份，不编译或下载。不是任意后端的兼容性承诺；当前复用已验证的原生RDMA服务二进制。`protoc`和Python依赖须已存在，不自动安装。

研究驱动只调用公开`scaleout topology/rdma-run/rdma-diagnose`。第一个正式无故障条件用于从实际任务图选取0→4 Dispatch，再生成故障规则，其余条件没有重复先行实验。13个条件分别有一个共享网络时钟，不能相加当一次推理。每次均须真实成功，任意意外失败立即停止，保存命令、退出和日志，不重试或改变预期。

单条件60秒/RSS1GiB/地址2GiB/64MiB、仿真5ms；全轮300秒/192MiB（含归档），tmpfs预留8GiB。运行目录必须不存在。平台监控覆盖实际服务子进程；研究命令记录自己的墙钟和累计目录大小。根卷只保存小报告，原件在易失存储，须另行备份才能长期保留。

分析器不导入平台内部程序，用实际包的序号/大小/线上端口复核捕获源的字节覆盖和重复量，独立重放源ACK计时，核对全部图与共同输入。源捕获仅覆盖GPU0和4，其余流量从全局计数核算，不能宣称全网逐包核验。三类篡改（源字节、提前超时、包payload）须拒绝。

输出包括逐条件有符号差值、源payload效率、GPU端口与全跳模型字节、业务完成与网络排空、目标包/ACK/超时证据。网络模型字节包含本仿真头部，不等同生产RoCE线上字节。无故障差距是条件比较尺度，不能视为任意优化的理论上界。

当前持久stream/事务入口拒绝旧fault/recovery模式；本轮仍是原生消息模型加真实LLMServingSim/ASTRA反馈，不是现代GIN、训练、千卡或生产故障率实验。20μs只在本场景较好，尚未通过不同反馈时延、路径和持久QP验证；原平台默认不变，完整目标继续推进。

`reproduce.py --input RUN_ROOT --output NEW_RECOVERY`生成有界本地证据检查点，在新源码根和空cwd离线执行分析器，比对三份确定性产物；不重跑网络。归档核验输入成员哈希，运行二进制/库仅保存身份，需实际重跑时仍须准备匹配运行环境。
