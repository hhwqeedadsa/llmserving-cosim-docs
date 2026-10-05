# 观测端口共享通信来源的验证记录

本目录保存消除端口间来源副本的实现对照。此前的[宿主预检](../m10_host_preflight/README.md)发现，每个端口各自复制全部传输和包来源。修改后，同一次服务的所有 `PortObservation` 共用一个 `WriteProvenance`，只保留一份传输描述符、一份包 UID 到业务字节范围的映射和一组源端监听；端口各自的计数、队列事件与输出仍独立。

[result.json](result.json) 保存输入、版本、命令、文件摘要和资源结果。这里描述该次修改及其验证，当前跨轮释放和存储规则见[后端说明](../../backend/README.md)与[记录与存储](../../docs/record_storage.md)。

## 核对的行为

每张图只登记一次来源。登记前完整检查本批身份，重复项、非法值或预算不足不得部分写入。共享表采用各端口中最严格的数量预算；关闭来源输出仍使用该表区分业务、元数据和填充。

删除一个端口不解除其他端口需要的监听。源 TP 回调只持有来源表；即使 TP 已从控制器移除，尚未完成的回调仍能安全查询，随 TP 销毁释放。本次修改尚未包含逐轮回收；该能力由后续[资源工程](../m10_resource_efficiency/README.md)补充。

各端口的 `registered_transfers`、`ledger_packets` 继续表示可查询数量；`owned_descriptor_records=0`、`owned_packet_provenance_records=0` 表示端口没有私有全量副本。内部 `provenance-audit.json` 记录实际共享表和回调数，不进入供关联方法读取的 `observed/`。

## 对照结果

原后端位于 `build/m10-fabric`，新后端位于 `build/m10-shared-provenance`。小型三 rank、四块合成模型执行两轮，16 B 分块，每次完成 2,400 次网络写、24 次本地交付、36,864 B 业务，均于 444,075 ns 结束。

| 观测出口数 | 原描述符条目 | 新描述符条目 | 原包来源条目 | 新包来源条目 |
|---|---:|---:|---:|---:|
| 1 | 4,800 | 2,400 | 2,400 | 2,400 |
| 2 | 9,600 | 2,400 | 4,800 | 2,400 |
| 10 | 48,000 | 2,400 | 24,000 | 2,400 |
| 10，关闭来源输出 | 48,000 | 2,400 | 24,000 | 2,400 |

新后端只连接九个源 TP，每次包创建事件只登记一次。逐组比较图、Chakra、网络日志、端侧/端口记录、队列和来源文件，分别有 55、66、154、104 个文件字节完全相同。只对独立核对过的构建路径和 protobuf 编译路径作规范化，未改业务时间、包 UID 或图内容。来源开关不改变执行。

对照串行运行，每组限采样 RSS 8 GiB、地址空间 12 GiB、300 s、产物 10 GiB。原/新峰值约 226～237 MB，波动不足以推出普遍性能比例；表条目不再随端口重复是本次确定的结果。逐端口窗口、文件和处理成本仍随端口数增长。

## 复现入口

从仓库根目录使用已安装的 Python 环境运行；两个构建必须分别对应原、新存储结构，输出目录必须不存在：

```bash
PYTHONPATH=python python tests/integration/check_shared_provenance.py \
  --baseline-build build/m10-fabric --build-dir build/m10-shared-provenance \
  --output-dir runs/shared-provenance-example
```

脚本检查物理存储和各端口可查询数量；C++ 专项另检查批次登记、预算、地址稳定及 TP 晚到回调。实际产物位于 `runs/v03-shared-provenance/comparison-final/`。原命令、逐项 RSS 和开发失败保留在[原验证说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_shared_provenance/README.md)。完整目标最终结果见[首轮验收](../m10_research_slice/README.md)。
