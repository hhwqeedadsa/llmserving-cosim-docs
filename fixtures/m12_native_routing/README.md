# 原生逐包喷洒输入

此例采用 2 个服务器、每服务器 1 芯片 2 die、2 个网络平面、每平面 3 个上层交换机。三个活动 rank 执行完整小型 Decode 模型，Dispatch 使用 STORE，Combine 使用 LOAD；状态与就绪通知实际传输。同芯片交付保留本地事件。模型及耗时是功能参考值。

在已安装前端和构建后端的仓库中运行，输出目录必须不存在：

```bash
python -m moe_ub_cosim.scenario run \
  --input fixtures/m12_native_routing/scenario.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service \
  --output-dir runs/native-spray
```

`scenario.yaml` 中 `ns3::UbLdstApi::UsePacketSpray` 为 `true`；改成 `false` 得到固定选路对照。`UseShortestPaths` 必须为 `true`。源端有两个平面出口，每个平面的下层交换机有三个上行候选；包不会在中途跨平面。沿用的预建 TP 不承载原生操作。

`fabric-audit.json` 保存声明范围，`run/execution/effective_backend.json` 回读实际 API 参数并检查原生源地址与交换地址的候选。`run/execution/ports/` 保存 32 个交换出口的实际流量和内部来源，`result.json` 保存任务和依赖执行；字段说明见[后端参考](../../backend/README.md)。

独立检查同时运行上述 MoE 与两份 65,537 B 原生任务，记录每个请求、响应的源端口和三跳交换路径：

```bash
cmake --build build/default --target cosim-native-routing-test -j 2
python tests/integration/check_native_routing.py \
  --build-dir build/default --output-dir runs/native-routing-check
```

检查包括固定/喷洒、选定上行链路 2,000 ns 时延、详细来源关闭、非法非最短路径。验证按实际路径与字节累计，不预先分配端口流量。目录中的 `result.json` 是已运行的功能证据摘要；它不表示硬件校准或完整目标规模验收。
