# 原生 STORE/LOAD 小型 MoE 输入

本目录提供可直接运行的输入：3 个 rank、真实请求组批、一个 MoE 层、CANN 消息布局、Dispatch STORE 与 Combine LOAD。LOAD 前的 32 B 供数通知在同一 UB 网络中实际传输。模型、路由和耗时均为功能检查用参考值，不代表目标硬件校准。

在仓库根目录、已安装前端依赖的虚拟环境中运行：

```bash
moe-ub build --build-dir build/default --jobs 2
python -m moe_ub_cosim.scenario run \
  --input fixtures/m11_native/scenario.yaml \
  --backend build/default/output/cosim/ns3.44-cosim-service \
  --output-dir runs/native-memory
```

输出目录必须不存在。`scenario.yaml` 中的路径相对该文件；命令行路径相对当前目录。`layer.json` 的 `native_profile` 选择向量操作，`backend-reference.yaml` 定义对应事务 profile。两者必须一致，改变操作必须重新生成图；把后端枚举改成 LOAD 不会自动修改旧 WRITE 图的依赖。

`scenario.yaml` 显式配置 3 个 worker、每 worker 两类 outstanding 各 2、8 个请求标识和关闭逐包喷洒；CBFC 由联合约束启用。预建 TP 来自可复用网络案例，原生任务不使用这些通道。 STORE 的任务按 worker 分段，LOAD 响应体可能含短尾填充，配置值不是硬件线程或指令的测量结果。

| 输出 | 内容 |
|---|---|
| `resolution.json`、`requested_backend.json` | 输入解析、来源与选择 |
| `network/backend.json` | 规范化网络配置 |
| `run/graphs/g*/plan.json` | 实际生成的节点、真实供数通知和 LOAD 依赖 |
| `run/execution/effective_backend.json` | 原生 API、worker 与网络对象的实际参数 |
| `run/execution/result.json` | 任务、节点和网络排空记录；原生开始/完成与 WQE 分开保存 |
| `run/execution/ports/8-4`、`ports/9-4` | 两个方向的端口观测和内部字节真值 |

独立验收使用以下命令，输出实际命令、退出状态、日志及 `summary.json`：

```bash
cmake --build build/default --target cosim-native-runtime-test cosim-plan-load-probe -j 2
python tests/integration/check_native_behavior.py \
  --build-dir build/default --output-dir runs/native-memory-check
```

原生运行时的请求标识复用、短尾与结束清理另由 `native_runtime_test.cc` 检查。平台进度和本机证据入口只在[当前任务](../../docs/tasks/current.md)维护；本目录不承担后续里程碑计划。

[检查摘要](result.json)保存实际命令、版本、退出状态与资源峰值。验收包括 66 个高位任务编号的原生任务、六种 MoE 对照、两组多层完整模型的 379 个原生任务，以及普通/有界压缩存储和导出的一致性。完整模型检查入口为：

```bash
python tests/integration/check_full_model.py --native \
  --build-dir build/default --output-dir runs/native-full-model-check
```

这些是功能证据；目标规模、逐包喷洒和硬件校准不在本组结果范围内。
