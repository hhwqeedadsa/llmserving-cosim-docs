# 操作级RC参考验收

2026-10-27。起点`62da06f`，事前范围`f817980`及其[源码核对后的明确边界](../../docs/tasks/rc_operation_reference_spec.md)。本增量交付[公开小例](../../examples/rc_operation_reference/README.md)和可复用Python状态参考，未修改MRT1、原生后端或任何依赖，也未运行新网络。

## 完成了什么

参考区分分段WRITE目标可见、累计确认、携旧值Atomic ACK、原子只执行一次、有限FIFO重放、发送原子额度和前缀完成。普通ACK不能提供原子返回值时，显式拒绝推定并保留原子信用和逻辑源保护。它使用无时钟的单QP、单注册区、小端64位内存；不是完整RC/GIN、NIC包窗口或生产成本模型。

11项单元方法通过，包括逐包可见和源释放、64位加法回绕、重复请求改变operand也只重放旧值、回复无需再ACK、信用/显式fence、普通ACK歧义、更晚ACK、序号NAK抑制、重放覆盖、ACK周期后增边界及非法输入/不支持PSN回绕。公开小例从空cwd运行，健康与丢反馈场景最终都是目标46、返回41、原子只应用一次。

## 固定源码与意外分支

Linux v6.12固定到[`adc218676eef25575469234709c2d87185ca223a`](https://github.com/torvalds/linux/commit/adc218676eef25575469234709c2d87185ca223a)。沿根目录→drivers→infiniband→sw→rxe的5个Git tree独立重建hash，核对9份源码blob；原3份缓存现在已补齐固定tree依据，旧审计当时“只有tag/SHA256”的记录保留。来源摘要见[identity.json](identity.json)，原API响应及上游源码只留本地归档，不复制进Git。

`tests/unit/rc_source_branches.py`提取13个未修改函数和完成器中2段未修改switch分支，使用明确的单线程内存、资源环、队列和网络替代环境编译执行。包字段/掩码及设备类型是替代定义，检查的是分支关系，未运行完整RXE、内核、NIC或真实重试调度。原子增量、返回值和队列头由小型确定输入提供。

| 原函数输入条件 | 实际结果 |
|---|---|
| 健康fetch-add：41加5 | 目标46，返回41，写回一次，完成后归还额度 |
| Atomic ACK丢失，直接重放原子请求 | 目标仍46，重放旧值41，未再次加5 |
| WRITE ACK及Atomic ACK均未交付，重复早先WRITE | 响应者发出普通累计ACK；所提取完成器完成两操作、归还额度，但原子返回区仍为初始哨兵值`0xdeadbeef`，没有执行写回 |
| 普通ACK的PSN晚于待完成原子 | 触发重试分支，未完成原子或归还额度 |
| 容量1的重放环已被后一原子覆盖 | 旧原子请求无响应，未重复执行 |

第三项从“先前WRITE已接收、其确认未交付”的显式状态开始；不是完整请求者/响应者执行记录。它揭示了必须验证的源完成分支，尚未证明完整栈可达性、当前Linux仍存在该行为或生产硬件有同类问题，**不称已确认漏洞**。

因此本参考明确采用`atomic_return_required/1`安全策略，与该RXE原分支不同；不宣称逐状态等价。后续原生接入必须保留操作类型、PSN、返回值和完成条件，不得盲目复制单一ACK完成规则。这个正确性基线本身不是新的网络恢复算法或论文创新。

## 证据和复核

原分支两次构建/执行的输出一致。独立核对器重查原函数字节与Git blob、公开小例状态，并拒绝伪造返回值、二次加法、遗漏写回和提前释放四类变更；见[verification.json](verification.json)。13固定依赖检查通过。两次生成失败保留：误在实现文件和局部头文件中查找响应枚举，最终从固定`rxe.h`取得；没有失败网络、重试同一URL或提高预算。

在已取得并核对来源的目录中，可复核：

```bash
python3 -B scripts/rc_reference_sources.py verify --root /path/to/upstream
PYTHONPATH=python python3 -B -m unittest discover -s tests/unit -p rc_reference_test.py -v
python3 -B fixtures/rc_operation_reference/verify.py --run /path/to/evidence --output /tmp/rc-verification.json
```

归档和复制源码/空cwd复现见[checkpoint.json](checkpoint.json)、[recovery.json](recovery.json)，资源和失败记录见[closeout.json](closeout.json)。详细运行根为`runs/rc_operation_reference_20261027/`；恢复只重新运行小型CPU检查、公开小例和既有替代环境二进制，不构建、不执行网络。

下一平台步骤仍需独立限定同一ns-3实例中的操作级包、QP序号/重放/反馈和真实完成接入；之后再做完整Hybrid及同协议强基线比较。当前未产生MoE时延收益、宿主提速、校准误差或千卡证据。两项研究候选和整体目标保持进行中。
