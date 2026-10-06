# 聚合保存与选择性详细记录

沿用 [M13 原生小场景](../m13_endpoint_service/scenario.yaml)，本目录的 `observation.json` 对节点 4 的五个出口保存 127 ns 基础窗口、VOQ/FIFO 等待统计及五类实际应用消息来源。默认不保存逐包事件；选定端口的详细区间按全局整数纳秒半开区间指定。字段、预算及再次采样的限制见[记录格式](../../docs/record_storage.md)。

公开运行先解析场景，再将生成的目录填入 `run.json.inputs`；默认示例路径如下：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim.scenario resolve \
  --input fixtures/m13_endpoint_service/scenario.yaml \
  --backend build/v04-platform/output/cosim/ns3.44-cosim-service \
  --output-dir runs/my-aggregate-input
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim run \
  --config fixtures/m15_aggregate_capture/run.json --output runs/my-aggregate-run
PYTHONPATH=python .venv/llmservingsim-native/bin/python -m moe_ub_cosim validate \
  --run runs/my-aggregate-run
```

自动检查从新目录生成输入，执行旧详细、新详细、纯聚合、指定端口区间详细、只保存计数发布来源五种配置：

```bash
PYTHONPATH=python .venv/llmservingsim-native/bin/python tests/integration/check_aggregate_capture.py \
  --build-dir build/v04-platform --output-dir runs/my-aggregate-check
```

检查实际执行、包时刻、窗口结果和来源逐项一致；队列任意边界重采样以独立逐纳秒积分核对。保存、检查、合并窗口和完全相同的后端重放均经过公开 CLI。细分窗口、移动边界、改变漂移或读取未记录范围，在创建输出前拒绝；详细行数和当前窗口来源条数越界也必须失败。原生来源在包到达后释放，退出时无残留。

旧数据兼容检查使用实际 v0.3 小数据包，比较 11 类执行记录及两份端口记录，重新结束于 35,538 ns。完整目标的旧结构化网络只做参数目录迁移与输入检查，没有在此重新执行大场景。迁移只补入 WRITE 不使用的原生编号参数及两处对齐说明；其他目录变化仍拒绝。脚本为 `tests/integration/check_legacy_capture.py`，所需历史数据未随 Git 提交。

实际命令、版本、退出码、资源与结果见 [result.json](result.json)。小例的短时 RSS 和耗时会有测量波动；记录压缩字节不等于包含图和输入的整个运行空间。完整规模与目标硬件校准不由本项小例证明。
