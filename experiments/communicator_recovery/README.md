# 通信域恢复与外部GIN内核边界

本研究核查现有Revoke、Shrink和用户GIN生产者的适用范围，为后续恢复模型保留合法前提和强基线。平台固定`917e42d`；没有新增平台API或GPU/provider/ns-3执行。预登记见[protocol](protocol.md)，研究判断见[中文报告](../../docs/reviews/通信域恢复与外部GIN内核边界_20261024.md)。

16份固定全文/20份摘录分别核长度、Git blob和SHA256。NCCL保存commit→tree→blob关系；DeepEP保存固定ref及指定commit树端点响应hash，不能把它冒称新增取得的commit对象。来源准备`sources.py --output NEW_INPUTS`复用9份缓存、取得3份小blob；再用`--cached nccl:src/include/nccl_device/impl/gin__funcs.h nccl:src/include/nccl_device/gin/gdaki/gin_gdaki.h deepep:deep_ep/include/deep_ep/comm/handle.cuh --additional src/include/nccl_device/utility.h --output SAME_INPUTS`补3份缓存和第4次小请求。各请求8秒/响应1MiB，总数最多6，重复获取直接拒绝。已有检查点应离线恢复，不重复已完成来源请求。

`audit.py build --inputs INPUTS --output NEW_BUILD`精确提取5个原函数和2条原文语句，以G++/C++17严格编译到`NEW_BUILD/source-harness`。CUDA、stream/proxy、child初始化、CQ/clock/fence均为记录替身；utility使用精确CPU版testAbort，不执行GPU atomic load。执行二进制向stdout写208条合成记录，分别为84条异步Revoke worker、20条Shrink、32条共享真值表、72条GIN wait。不存在GPU故障率、真实耗时或合法并发证明。

`audit.py evidence --inputs INPUTS --output EVIDENCE.json`生成摘录；`audit.py analyze --records RECORDS --output SUMMARY.json`汇总。`verify.py`独立读取原文和全部记录，不导入提取器、harness或平台测试；用整数及调用顺序oracle核对208条条件，拒绝4类错误结论和一次轮询次数篡改。无deadline/无abort分支的记录停止不是原函数返回或实机死锁。

```bash
python3 -B experiments/communicator_recovery/bundle.py restore \
  --archive runs/communicator_recovery_checkpoint_20261024/checkpoint.tar.gz \
  --output /dev/shm/communicator-recovery-restored

PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/communicator-recovery-replay-monitor \
  --rss-bytes 1073741824 --address-bytes 2147483648 \
  --artifact-bytes 16777216 --wall-seconds 60 --min-free-bytes 8589934592 \
  --artifact-root /dev/shm/communicator-recovery-recomputed -- \
  python3 -B /dev/shm/communicator-recovery-restored/source/experiments/communicator_recovery/bundle.py replay \
  --stage /dev/shm/communicator-recovery-restored/evidence \
  --output /dev/shm/communicator-recovery-recomputed
```

恢复先逐成员检查512成员/16MiB未压缩/8MiB压缩上限，再验证所有普通文件、路径、父子文件冲突和manifest/hash，最后创建目录；不执行包内程序。manifest是一致性清单，不是发布者认证。`bundle_checks.py --output NEW_DIR`检查8类非法归档。离线复算从新源码根/空cwd重新编译并执行CPU片段，拼接源码、全部记录、摘录、摘要及独立核验五份产物应逐字节一致；只需已有Python标准库、G++和libc，不下载或安装依赖。同Linux宿主复算不能证明跨硬件行为。

`results/20261024/`保存小结果、来源及恢复/预算摘要。检查点保留完整源码/响应、失败准备器、首次失败编译、全部CPU记录和命令/退出/监控；打包后的replay与closeout单独保存。初次准备器的两项缓存问题和替身宏错误未改原函数字节，也没有重复成功请求或已完成CPU检查。

研究结论是排除错误恢复模型。正常provider强退出、协同排空、现有Revoke/Shrink和独立分配/备用窗口仍须同资源比较。外部生产者终止、逐MR/DMA退出、真实故障调用合法性、现代完整接入、代表性负载、校准、规模及论文贡献尚未完成；不能用本轮核查替代整体目标。
