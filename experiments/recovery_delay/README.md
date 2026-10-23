# 反馈时延与丢包恢复

本实验检验20μs固定超时的适用范围，以及已有Karn估计、每源历史和保守初值这些强基线。先完成45个预登记条件，再在看见冷启动问题后登记9个Karn80补充，保留原结果并明确区分。研究约束见[protocol.md](protocol.md)，结果见[阶段报告](../../docs/reviews/ScaleOut恢复稳健性与清理交付_20261023.md)。不将经典算法调参当作论文创新。

只使用公开topology/rdma-run/rdma-diagnose命令。`service.json`是合成MoE Decode小任务，`topology.json`是8GPU/100G Clos，`protocol.json`固定源额度和观测范围。GPU名为描述，计算为声明值，没有GPU执行或硬件校准。一个条件的各组和步骤共用同一原生网络时钟。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 /ABS/FRONTEND_PYTHON -B experiments/recovery_delay/run.py \
  --backend /ABS/NATIVE_SERVICE --runtime-manifest /ABS/BUILD_VERIFICATION_JSON \
  --library-manifest /ABS/LIBRARY_HASH_JSON --protoc /ABS/PROTOC --output /dev/shm/NEW_DELAY_RUN
```

runtime manifest提供服务二进制hash与9个构建源码hash，library manifest提供8个固定原生.so的路径和hash。它们来自实际构建收据；缺失不能凭空生成期望hash。锁定依赖及公开构建入口见[原生后端](../../examples/scaleout_rdma/README.md)。Python须有现有前端所需依赖。以上路径显式提供，不依赖旧实验驱动、tests或fixture。

Karn80补充使用同一程序，再加`--bootstrap-baseline /dev/shm/NEW_DELAY_RUN`，`--output`改为该目录下全新的`bootstrap80`。两轮累计300秒/256MiB，不能分别重新取得整轮预算；脚本把原轮记录时间计入。网络单例仍受60秒/RSS1GiB/地址2GiB/仿真5ms/结果16MiB约束。配置只用于这个固定研究，不是任意模型参数的自动调优器。

外部控制器曾在第六条件网络启动前中断，已核对原进程退出、仅有四份输入文件、没有execution/结果或网络日志；原输入与零长度日志保存在`pre-network-interruption/`。`--resume`只复用hash核对的已完成前缀，并保留这种未启动输入；遇到任何网络执行前缀或服务失败均拒绝重跑。它不会删除失败或自动增限。原45条件各实际执行一次；Karn80是另9个条件。

```bash
python3 -B experiments/recovery_delay/analyze.py \
  --input /dev/shm/NEW_DELAY_RUN --output /dev/shm/NEW_DELAY_RUN/analysis54
python3 -B experiments/recovery_delay/reproduce.py \
  --input /dev/shm/NEW_DELAY_RUN --expected-analysis /dev/shm/NEW_DELAY_RUN/analysis54 \
  --output /dev/shm/NEW_DELAY_RUN/recovery54
```

分析器独立重放源端序号、实际ACK进展、计时器、Karn估计和每源有限历史；没有导入平台执行器或测试。输出summary.json/results.md/report.html；记录初值更改能解决的问题和仍保守的反例。离线归档校验每成员hash，在新源码根与空cwd复算三份产物，网络不重跑。主轮45条件版分析与归档也保留，最终以54条件版为准。

所有输入、代码与命令版本化；大型原件默认在易失内存盘。当前本机归档位置及hash以报告的closeout/persistent_backup记录为准。归档包含离线分析所需输入，不包含二进制、共享库或Python依赖，不能称完整部署环境备份。生产故障率、千卡性能、现代GIN及硬件精度仍需其他证据。
