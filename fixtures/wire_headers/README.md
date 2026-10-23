# 实际报文头的被动观测验收

为同端点多QP的ACK关联增加可选IPv4/端口观测，不修改线上包或调度。输入为本目录[六项工作](work.json)，四条连接包含正反方向、两平面、同端点/相同累计序号而不同线端端口；不是模型流量或性能评测。

公开验收命令（`NEW_STREAM`为按当前源码构建的原生持久入口，`OLD_STREAM`为修改前的对应二进制）：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_wire_headers_workflow.py \
  --backend "$NEW_STREAM" --reference-backend "$OLD_STREAM" --output /dev/shm/NEW-wire-check
```

脚本从新cwd使用公开CLI创建8GPU/10交换机双平面拓扑，不依赖历史运行输入。由宿主监控器执行时每命令60秒、RSS1GiB/地址2GiB、产物64MiB，仿真上限5ms、结果16MiB；卷预留8GiB。实际命令和退出状态写入输出的`commands.json`。

结果见[功能验收](verification.json)、[构建](build.json)和[受影响回归](regressions.json)：7条公开命令和1条原生拒绝；616事件/28头表项；5组端点与序号相同的ACK用实际端口区分。默认新旧完整结果逐字节一致，开启与显式关闭只增加声明字段；截断保存前缀和节点/时间筛选精确相符。原生绕过Python时也拒绝非布尔开关。7项RDMA输入检查、30条本地阶段命令及本地copy/事务流程通过，固定13依赖一致。

首次验收的测试拓扑超出默认16交换机上限，在执行网络前失败；原脚本及日志保留，缩小测试拓扑后验收，没有提高限额。构建14步完成，所有Fabric消费者使用一致的新布局，旧二进制和8个原生库hash不变。原件位于`/dev/shm/moe-wire-headers-20261023/`，仅易失存储。

表只覆盖已保存事件，UID仅为包lineage。UDP17与原生qbb ACK252/NACK253端口来自实际头；此小例实际覆盖17/252，不声称验证NACK或其他协议的全部组合。不提供窗口外关联、算法可读真值、硬件校准或性能优化结论。
