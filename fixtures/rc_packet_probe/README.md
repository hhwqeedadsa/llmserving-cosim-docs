# RC操作包的独立验收

事前范围见[规范](../../docs/tasks/rc_packet_probe_spec.md)，普通操作见[公开小例](../../examples/rc_packet_probe/README.md)。这组功能检查不是研究实验驱动；旧网络条件不重开。

`verify.py`用独立的Python大端字段读取解码实际包字节，再将实际收包依序交给公开`RcRequester`/`RcResponder`状态参考，核对逐包写入、有限原子重放、旧值、源完成、物理链路与收发守恒。完成时刻来自收到真实反馈的事件，不按固定传输时间计算。对目标内容、返回值、路径、操作码及重复副作用分别作篡改拒绝。功能正确不等于Linux整栈、真实网卡或生产网络等价。

保存标准目录后可离线核验：

```bash
python3 -B fixtures/rc_packet_probe/verify.py --run /path/to/rc_packet_run --output /tmp/new-verification.json
```

标准目录包含`inputs/`、四个条件`healthy/`、`drop_write_middle/`、`drop_write_ack/`、`drop_atomic_ack/`以及`legacy/`，每个条件的`result.json`保留原始输出。原始日志、构建和完整结果不提交Git；结果摘要及归档信息在验收完成后保存于本目录。

本次验收完成：见[中文网页](report.html)、[独立核验](verification.json)、[来源身份](identity.json)、[恢复记录](recovery.json)和[资源收尾](closeout.json)。四RC条件及一个新legacy小例各执行一次，24项相关单元与13项固定依赖检查通过；两次构建中首次缺头文件失败、修复后第二次成功，原日志/源码保留。未执行完整MoE或旧研究网络。

健康条件4请求/2回复包，36次逐跳TX与36次RX；三个丢包条件各一次RTO、一次物理丢包。独立重算目标效应/返回旧值、完成、全部物理路径/包字节和队列占用/面积，5类证据篡改拒绝。丢回复后原子仍只应用一次，旧值41、最终46；这些关系是功能正确性，不是加速或优化空间。

归档为`runs/rc_packet_probe_20261027/checkpoint.tar.gz`，SHA256 `f69837d827e98cced8ccb4bcbebd60f44e88c7d0e5e248bc1e1d435a6d3938cb`。299个内容成员逐hash恢复后，复制源码/空cwd重算14份输入、核验JSON及HTML逐字节相同，并通过3项输入/11项原状态参考检查；恢复零网络/构建/HTTP/GPU。不包含二进制，不声称完整构建的离线复现；依赖bundle需要[身份记录](identity.json)声明的前置提交。

`checkpoint.py create/restore`保存和恢复这些材料；归档只选择已有终态收据的monitor，避免将自身正在增长的日志当稳定证据。原首版包hash无不一致，但选择了活跃monitor，后续加固并保存首版；原网页链接也留有修正前副本。上述维护修正均未重跑网络。
