# 持久网络与因果本地阶段验收

本增量补齐“网络回调→本地处理→后续发送”的功能链。全部通信与本地处理共用锁定ASTRA原生ns-3的时钟，连接和控制器状态在处理期间继续存在；不混入UB库。输入格式、事件及输出见[公开示例](../../examples/scaleout_rdma_streams/README.md#网络回调与本地处理交替执行)。

## 检查内容

- [verification.json](verification.json)：23条公开CLI与7条原生绕过CLI检查。正常示例3次写、5项本地处理，共18个源数据包、15,482B有效载荷。检查器从实际目的端数据包和源端累计ACK重新计算每次写的接收/确认时刻，再推导本地入队、排队、执行和后续发布，不使用平台调度函数生成预期。
- 同一GPU上的本地工作串行，不同GPU可以重叠；GPU1的独立400,000ns工作约束最后一次写。零时长也执行真实完成事件；最后本地工作比最后ACK晚完成，不提前关闭QP。另一用例专门检查输入序号较大的已排队工作先于较小序号的新解锁工作。
- 慢上联功能用例在执行前固定25G上联和1/2B标记阈值，保持100G接入；仅用于产生可核查的拥塞反馈。GPU4的200,000ns本地阶段内，stream0确有2个`rp_timer`事件；QP创建数仍为1、后续序号延续。此人工极低阈值不是生产配置，不比较性能收益。
- v1/v2分别在默认及独立反馈参考模式下与旧二进制逐字节相同。只写的v3去掉3项新增顶层字段后与旧结果完全相同；正常输入重复结果逐字节相同。
- 7类非法输入分别通过CLI和原生入口拒绝，包括布尔时长/卡号、非GPU rank、跨类型完成事件、绝对就绪时间、错误模型及kind。5ms截止用例保存实际前缀：仅第一个写被提交，后续写没有预留/发布；在执行和排队的本地工作不冒充完成。
- [regression.json](regression.json)：既有持久字节流程15条公开CLI和1条原生检查通过，含24连接/192工作、深度与并发根工作、重复及拒绝。规模仍为8GPU，不宣称千卡验收。
- [maintenance.json](maintenance.json)：13项Python单元检查、13项固定依赖、增量构建与资源。首次编译因JSON隐式转换产生歧义而失败，显式取unsigned后修复；失败日志保留，预算未扩大。另一次文档补丁因标题不匹配而未应用，已按实际标题修正。
- [archive.json](archive.json)、[replay.json](replay.json)：有界检查点与从新源码根/工作目录复核的摘要；完整原始件只在`/dev/shm/moe-local-stages-20261023/`，易失且无持久备份。保留的ELF含本机RPATH，不是可移植镜像。

本地时长来自输入声明，每GPU只有一个抽象串行槽。没有GPU成本校准、SM/显存/并发CUDA、现代DeepEP调用、self-peer GIN路径或GPU可见性；`accepted`仍只是接收字节前缀。本轮是平台功能验收，没有验证生产负载代表性、优化收益或论文创新。

## 复核

在仓库根运行，两个二进制分别是本增量及修改前的原生`stream_probe`；不能使用UB程序。输出目录须不存在、父目录须存在：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_local_stage_workflow.py \
  --backend /ABS/NEW_NATIVE_STREAM_PROBE \
  --reference-backend /ABS/OLD_NATIVE_STREAM_PROBE \
  --output /dev/shm/my-local-stage-check
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p scaleout_streams_test.py
```

用已有`host_probe`限制每命令60秒/RSS1GiB/地址2GiB/产物64MiB，卷预留8GiB。仿真上限5ms、结果16MiB；本阶段累计受监控时间300秒、192MiB，归档成员128MiB、压缩48MiB。完整实际命令、cwd、退出码与日志保存在检查点中；失败不扩限重跑，不安装下载或更改固定依赖。
