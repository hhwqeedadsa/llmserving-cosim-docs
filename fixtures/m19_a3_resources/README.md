# A3 局部资源与完成依赖测试输入

`layer.json` 用三个 rank、六个专家和 Top-2 检查同 rank、同芯片与跨芯片交付，H=1025 用于检查非对齐短尾。`service.json` 与 `layers.json` 是独立的小型完整模型输入：H=1024、三个连续 MoE 层和三个请求，通信核数显式设为 2，默认复制栅栏等待目标数据就绪。来源与假设见 [M18 核对](../m18_a3_execution/README.md)。这些是合成功能输入，不代表 R1 路由或硬件测量。

模板为 `cann_a3_v850_store_resources_v1`，不使用旧全 rank 一至两个未完成 SEND 的配额。每个通信核分别执行局部工作，同核缓冲复用等待该数据的实际原生请求形成；原生 worker 和节点请求号池继续限制在途通信。计算、状态数量和数据字节的定义保留，源事务资源仍需完整回收。

单元检查位于 [a3_plan_test.py](../../tests/unit/a3_plan_test.py)，检查累计工作量、字节与消息数、单/双缓冲阈值、Token 独立归约、连续层边界、容量、轻量预检计数和非法依赖。后端检查位于 [check_a3_resources.py](../../tests/integration/check_a3_resources.py)，使用生产服务入口和实际网络，核对独立核重叠、真实等待事件、部分 Token 先归约、回调唯一性、资源最终释放及 Python/C++ 计划一致性。

```bash
PYTHONPATH=python python -m unittest discover -s tests/unit -p a3_plan_test.py
PYTHONPATH=python python tests/integration/check_a3_resources.py \
  --build-dir build/default --output-dir runs/a3-resource-check
```

实际执行须在明确的宿主时间、内存和累计磁盘预算下进行。第二个命令要求相应构建目录已有 `cosim-service` 和 `cosim-plan-load-probe`；测试程序本身不构建、不下载数据，不清理旧结果。普通用户入口由平台用户指南提供。

## 验证证据

M19 已通过小型实现验收；四类测试的最终汇总、公开有限层段和目标规模验证由 M20/M21 继续完成。

- `runs/m19-a3-resources/python-regression-2/`：15 项 A3、完整模型和历史路由预检单元检查通过。
- `runs/m19-a3-resources/python-export/`：7 项历史图存储及导出检查通过，旧计划和 Chakra 文件保持逐字节一致。
- `runs/m19-a3-resources/resource-unit/`：ASTRA 旧槽规则及新增通信核资源占用/释放检查通过。
- `runs/m19-a3-resources/native-regression/`：旧 STORE/LOAD 六组执行、记录被动性、有限请求池和非法输入检查通过。
- `runs/m19-a3-resources/a3-integration-3/`：单层及完整模型保留模式核对、4 种篡改拒绝、两轮基准和一轮部分到达执行通过；摘要见 [result-summary.json](result-summary.json)。
- `a3-integration-1/` 保留内部重建整数类型不匹配的失败；`a3-integration-2/` 保留零时长事件编码缺少显式 `num_ops=0` 的失败。分别修正重建类型和事件导出/后端校验，不删除断言、不改变输入工作量来绕过。

新构建及检查累计占用约 41.2 GiB，低于延续的 48 GiB 任务产物上限；计入的历史目录清单见本地 `host-policy.json`。首次联合构建约 538 秒、采样进程组内存峰值 1.69 GiB，均在 1800 秒/8 GiB 预算内。后续增量构建与测试分别记录，没有提高预算。

本地每项的实际命令、退出状态、日志及资源峰值在对应 `probe.json` 和 `results/commands.json` 中。没有运行 E 实验或完整 61 块目标场景。
