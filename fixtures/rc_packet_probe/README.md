# RC操作包的独立验收

事前范围见[规范](../../docs/tasks/rc_packet_probe_spec.md)，普通操作见[公开小例](../../examples/rc_packet_probe/README.md)。这组功能检查不是研究实验驱动；旧网络条件不重开。

`verify.py`用独立的Python大端字段读取解码实际包字节，再将实际收包依序交给公开`RcRequester`/`RcResponder`状态参考，核对逐包写入、有限原子重放、旧值、源完成、物理链路与收发守恒。完成时刻来自收到真实反馈的事件，不按固定传输时间计算。对目标内容、返回值、路径、操作码及重复副作用分别作篡改拒绝。功能正确不等于Linux整栈、真实网卡或生产网络等价。

保存标准目录后可离线核验：

```bash
python3 -B fixtures/rc_packet_probe/verify.py --run /path/to/rc_packet_run --output /tmp/new-verification.json
```

标准目录包含`inputs/`、四个条件`healthy/`、`drop_write_middle/`、`drop_write_ack/`、`drop_atomic_ack/`以及`legacy/`，每个条件的`result.json`保留原始输出。原始日志、构建和完整结果不提交Git；结果摘要及归档信息在验收完成后保存于本目录。
