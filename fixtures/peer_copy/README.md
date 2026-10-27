# 机内 peer 转发验收

本增量使同服务器的 GPU 可以转发实际窗口字节，并与原生持久跨机网络共用一个时钟和持续存在的 NIC 状态。它复用已有分块 crossbar，不将 peer 流量注入机外 RDMA，也不重新生成后续发送数据。用户配置与公开操作见[小例](../../examples/scaleout_peer_copy/README.md)，平台范围见[规范](../../docs/tasks/peer_copy_spec.md)。

可直接查看[说明网页](report.html)或[独立核验](verification.json)。这是人工功能验收，不是完整 Hybrid、真实推理、千卡执行或论文收益实验。

## 已核验结果

- 一次全新公开构建通过，437个编译单元有效 `-O3`，保留警告；峰值受监控进程组RSS约1.55GiB，构建约405秒。13项固定依赖未改变。
- 7项新准入、11项静态校验、17项事务及13项持久流检查，共48项通过；5个受影响C++编译单元先通过语法检查。
- 新cwd的15条公开CLI覆盖两次帮助、两种拓扑、7类预期拒绝及4次实际网络。非法输入均在创建输出和启动后端前拒绝。四次预定执行全部通过，之后不增加网络。
- peer主例：3次机内copy交付512B，两条跨机put各256B；独立解码两份实际MRT1源载体共576B，逐项重放20个内存事件与最终字节。
- 两个源共享GPU2目的端口的完成时刻为276/340ns，目的端口繁忙256ns、最大等待128ns；后续256B机内copy耗时340ns。独立逐chunk排程与全部端口统计一致。
- 实际物理记录中共24次逐跳发送及对应接收；逐端口、字节、序列、传播/序列化、接收/ACK与应用回调核对通过。peer没有增加物理RDMA包或ACK；messages中的兼容ACK字段只表示本地数据就绪。
- 实际跨机接收6496ns → GPU4到GPU5的peer完成6836ns → GPU5发出后继数据。首个发送方到12520ns才收到完整ACK；全部网络在19016ns排空。这些数值只属于本例的声明参数。
- 直接probe与依赖session的窗口/事务/机内流/端口/本地工作和结束时刻相同。迁移前后完整result只允许逐hash核实的八库路径和枚举顺序差异。
- 7类证据篡改被独立核验器拒绝。原始失败不是网络执行失败：最初两项单元检查遇到旧stream-only准入限制，原源码、错误及修正说明保留于检查点。

## 恢复与来源

[archive.json](archive.json)登记613成员、约7.1MiB的本地检查点，包括实际12个运行时文件（两个程序、两个启动器、八库）、输入、源代码、编译身份、原始结果、命令和失败记录。[recovery.json](recovery.json)记录逐成员hash校验、复制源码/空cwd的四份离线重算，以及网页逐字节一致；恢复没有运行网络或构建。[source_basis.json](source_basis.json)区分已有crossbar/内存保护、固定原生网络和声明的模型假设；[closeout.json](closeout.json)记录资源与额度关闭。

原件目录为 `runs/peer_copy_20261027/`，归档本体不提交Git，长期备份仍按原存储策略处理。原构建/执行时平台基于`695f44e`加本次工作区改动，准确二进制身份以归档中编译源码hash、编译选项及实际库hash为准。不能仅凭旧HEAD断言已包含新实现。

## 复核入口

`verify.py`只读原始配置、实际结果与拓扑，用独立字节数组和端口排程复核，不导入平台执行器。`accept.py`冻结并运行本规范四个网络；使用前需按独立预算执行公开构建。`closeout.py`负责解释网页、封存及恢复。普通用户操作仍通过公开CLI，无需这些验收脚本。

```bash
PYTHONPATH=/ABS/REPO/python:/ABS/REPO python3 -B -m fixtures.peer_copy.verify \
  --result /ABS/RUN/peer/result.json --config /ABS/RUN/inputs/work.json \
  --topology /ABS/RUN/topology/topology.json --protocol /ABS/RUN/inputs/rdma.json \
  --output /ABS/NEW/verification.json --corruptions
```

命令应放在公开host_probe的60秒/RSS1GiB/地址2GiB及累计磁盘限制内。该复核没有网络。peer模式不提供真实NVLink、TMA、GPU内存一致性、注册撤权或现代GIN成本；完整Hybrid的slot、计数、屏障、Combine及强基线比较仍未完成，两项论文创新及总目标保持进行中。
