# 全模型存储优化的验证记录

本目录记录 `full_model.py` 合并图时减少重复对象和复制的修改。内部构图保留自己创建的子层图；重构校验只读子层，仍逐层验证、重建全部合并节点和传输并逐字段比较。对外合并器继续复制调用方输入，可变记录互不影响。没有删除校验、改变计划格式或修改网络执行。

## 等价性依据

[golden.json](golden.json) 由修改前的 `a828c0a` 保存，固定四组完整图的字段与列表顺序摘要。用例包括单 MoE、dense 穿插、无 shared expert、八块/32 请求/小分块、不同上下文、零 Token 成员和非默认身份。

检查同时覆盖输入隔离、返回图不受后续输入变化影响、校验不修改被检图，以及来源、字节、依赖和阶段成本篡改拒绝。较大测试输入最初使用 1 MiB CANN 窗口，被旧校验拒绝；显式改为 8 MiB 后才建立有效基线，生产约束未变，原失败日志保留。

## 当次测量

六块对照使用同一首轮 bundle 的前三个 dense 块和后三个 MoE 块，保留 128 rank、1024 请求和实际来源。

| 指标 | 旧实现 | 本次实现 |
|---|---:|---:|
| 构图及完整校验 | 80.347 s | 45.437 s |
| 含读取、摘要和释放的进程时间 | 109.347 s | 73.827 s |
| 采样峰值 RSS | 2,785,492,992 B | 1,874,989,056 B |
| 节点/交付/依赖边 | 399,468 / 194,952 / 7,180,604 | 相同 |

完整计划及逐层来源摘要相同。该次峰值下降约 32.7%、构图时间下降约 43.4%；测量没有运行网络，不代表所有输入的固定收益。

完整 61 块首轮于 876.585 s 在内存中构造并通过全部校验，包含 7,655,238 个节点和 3,767,694 次交付。峰值 RSS 为 33,905,291,264 B，约 31.58 GiB，低于当次 32 GiB 上限。随后通用导出器再次重构校验，在 900.015 s 触发 `wall_limit`；外层退出 1、worker -15，无残留进程。没有完整导出的 JSON/Chakra，`built.json` 只是计数。目标加载和网络执行在该次测量中未开始。后续直接构图/导出的对照见[下一项记录](../m10_full_model_export/README.md)。

24 项单元检查、完整模型服务及非法计划拒绝、61 块小 EP、穿插 dense/无 shared expert、实际 C++ 加载均通过。三组历史小图共 15 个文件逐字节一致；13 个依赖和 1,304 个受保护文件摘要未变。

## 复现与证据

```bash
PYTHONPATH=python:tests/unit python -m unittest \
  full_model_storage_test full_model_test full_round_probe_test \
  resource_budget_test cann_index_test multilayer_test
```

`scripts/probe_full_model_storage.py baseline|optimized --output-dir ...` 需要固定旧提交和原首轮 bundle，必须受 `moe_ub_cosim.host_probe` 监测：六块对照限 300 s、RSS 8 GiB、地址空间 12 GiB、产物 1 GiB。完整首轮沿用[预检预算](../m10_full_round_preflight/README.md)，输出目录必须不存在。

[result.json](result.json) 保存输入、源码、阶段与回归摘要，实际命令和日志位于 `runs/v03-full-model-storage/`。被压缩的逐阶段表与失败记录可查[原说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_full_model_storage/README.md)。本文件保留该次受限测量；后续完整首轮成功结果见[最终验收](../m10_research_slice/README.md)。
