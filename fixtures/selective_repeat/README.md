# 有界选择重传参考基线验收

对应[规范](../../docs/tasks/selective_repeat_spec.md)和[公开示例](../../examples/scaleout_rdma_streams/README.md#可选的有界选择重传)，父基线`c01cf2d`。增量构建持久字节适配器，固定八个原生ns-3库只读复用；没有依赖修改、生产RoCE或IRN复刻。此阶段补强对照能力，不是论文算法或性能收益。

## 实际覆盖

| 条件 | 源数据包数 | 超时重试数 | stream0乱序峰值B |
|---|---:|---:|---:|
| 正常 | 27 | 0 | 0 |
| 首包丢失 | 18 | 0 | 3000 |
| 中间包丢失 | 17 | 0 | 2000 |
| 尾包丢失 | 16 | 1 | 0 |
| 首包和一次选择反馈丢失 | 17 | 0 | 3000 |
| 最后累计确认丢失 | 16 | 1 | 0 |
| 实际交换机间链路关闭 | 31 | 1 | 0 |
| Karn尾包恢复 | 16 | 1 | 0 |

这些条件工作量不同，表只描述功能执行，不能按包数横向计算优化收益。正常例含两个连接、四项写及一项本地处理：本地阶段在实际ACK的50,586ns开始，150,586ns结束后解锁17B追加写。

另一个显式拥塞例产生258个源数据包、15次目的端CE通知输入及4个实际生成/发送/到达的CNP，峰值交换队列67,340B，证明参考拥塞控制和选择重传可以共存；未作硬件校准。默认旧/新二进制完整结果JSON相同。第12次实际网络在新Python源码根、空cwd经两条公开CLI运行，完整结果除加载库路径外相同，八库实际从包内加载。共12次实际网络，达到本阶段上限，不再追加执行。

纯状态核心以逐字节集合为独立oracle，枚举720种顺序×6种容量，并覆盖4096项元数据边界、非法反馈与重叠，共578,671项检查。13项原有流输入、5项新增选择重传、8项持久恢复输入检查通过，共26项不同Python测试；最后一组原fixture缺少必填`delay_ns`，补为1000ns，保留最初4项KeyError日志，没有放宽校验。

`audit.py`不导入传输实现，按实际源TX、接收和反馈到达重放逐字节接收/源已知集合，核对窗口、缓冲、真实MSR1扩展、累计完成、工作依赖和快速重试；逐物理边/发送次数核对序列化、传播、丢弃与队列积分/峰值及最终MMU。9个选择重传条件全部通过。另由`checks.py`分别在Python和原生入口拒绝12类非法配置，并拒绝4类证据篡改。原生非法输入在`Simulator.Run`前结束，不计入12次实际网络。

## 复核与证据

原件在`/dev/shm/moe-selective-repeat-20261024/`，持久检查点在`runs/selective_repeat_checkpoint_20261024/`。Git保留[功能摘要](verification.json)、[离线恢复摘要](recovery.json)和[预算收尾](closeout.json)；完整原始结果、失败、命令和终态监控随包保存。包为收尾前快照，后补收据单独保留。

仅离线复算已有结果，不执行新网络：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B fixtures/selective_repeat/checkpoint.py restore \
  --archive runs/selective_repeat_checkpoint_20261024/checkpoint.tar.gz \
  --output /dev/shm/selective-restored
cd /dev/shm/selective-restored/runtime/empty-cwd
PYTHONPATH=/dev/shm/selective-restored/source/python PYTHONDONTWRITEBYTECODE=1 \
  python3 -B /dev/shm/selective-restored/source/fixtures/selective_repeat/audit.py \
  --stage /dev/shm/selective-restored/runtime --output /dev/shm/selective-audit.json
```

恢复在创建目标目录前检查成员类型、路径、重复、大小、完整集合及SHA256；同主机增量二进制重放已完成，不是从零构建或跨主机保证。`build.py/run.py/replay.py`是本阶段验收驱动，必须在重新声明资源预算后使用，不能当作无代价离线检查。特别是`replay.py`默认会执行网络；其`--audit-existing`仅用于尚未生成replay收据的已完成流程。

便携流程最初的检查器直接比较含`bin/../lib`的字面路径与规范路径，误报失败；修为规范化后逐库核hash，仅离线接续，原网络没有重跑。两项准备/检查失败均保留。MSR1扩展16B替换原14B填充，实际模型帧50B、净增加2B；有限逻辑payload容量不等于宿主Packet总内存或实测NIC缓冲。

后续研究须在相同工作量、显式窗口/缓冲和线上开销下重新比较；此处不能证明先前真实路由故障的业务损失可消除。现代Dispatch—计算—Combine依赖、GIN/self-peer资源、硬件校准、独立代表性请求及千卡完整执行仍缺，整体论文目标保持进行中。
