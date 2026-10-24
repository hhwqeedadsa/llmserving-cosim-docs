# 交换机缓冲启动前检查验收

`scaleout_fabric_buffers`把固定原生Fabric的PFC headroom/reserve约束提前到Python共同入口，并提供公开`scaleout rdma-preflight`。不修改C++、固定依赖、配置或包执行，不需要构建/下载/GPU。

7项新单元覆盖整数舍入、严格容量边界、单端口/累计溢出、端口/类型拒绝、公开命令及在线未启动检查。相关`scaleout*_test.py`共109项（含新增7项），`transaction*_test.py`17项通过，共126项不同检查；13项固定依赖核验通过。三个历史单元的人工拓扑补上了原生必需的`delay_ns=1000`，没有修改生产输入默认值。

`native-reference.json`保存已验收平台`2cfbaf8`实际结果中的32个交换机端口字段，完整结果hash和来源命令在其中；源码hash固定Fabric与SwitchMmu。它是既有原生输出摘录，不是新的网络运行，也不是硬件校准。新离线报告32端口headroom及初始PFC阈值全部相同。

`verify.py`执行10条公开命令：新目录拓扑、合法报告、无序/有序事务、非法容量报告，以及5个运行入口的启动前拒绝。非法反例的GPU0接入延迟250us，使交换机8需9,503,884B>8,388,608B；五入口均在backend启动及运行目录创建前拒绝。可执行标记程序确认未被调用。输入错误不会自动扩容或改变研究条件。

在仓库根使用不存在的新目录，按60秒/RSS1GiB/地址2GiB/16MiB/8GiB预留监测：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -B -m moe_ub_cosim.host_probe \
  --output-dir /dev/shm/my-fabric-check-monitor --artifact-root /dev/shm/my-fabric-check \
  --rss-bytes 1073741824 --address-bytes 2147483648 --artifact-bytes 16777216 \
  --wall-seconds 60 --min-free-bytes 8589934592 -- \
  python3 -B fixtures/fabric_buffer_preflight/verify.py --stage /dev/shm/my-fabric-check
```

该流程只用当前代码、版本化参考和公开小例；不读取旧运行目录，不启动原生网络。原始监控在`/dev/shm/moe-fabric-preflight-20261024/`，小型持久摘要在`runs/fabric_preflight_20261024/`，验收/资源摘要为同目录`verification.json`、`closeout.json`。通过仅证明静态初始保留量与已有事务规则，不证明动态无损、延迟准确、千卡执行或A类论文贡献。
