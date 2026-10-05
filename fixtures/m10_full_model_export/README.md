# 构图后直接导出的验证记录

本目录记录减少重复全模型校验的修改。`generate.build_export_full_model` 在一次调用中构图、完成原有子图验证及完整重构比较，然后立即写出；返回之前，调用方拿不到可变计划。原 `export_plan(plan, output)` 仍完整校验外部传入的计划。没有增加跳过校验的参数、跨调用缓存或修改计划的回调。

写出函数从原导出器提取，Chakra 编码、JSON 格式、摘要、预算和清单保持。该步骤只改变完整首轮测量入口；当时普通服务仍走通用导出路径。当前模块关系见[架构说明](../../docs/architecture.md)。

## 对照依据与结果

[baseline.json](baseline.json) 固定旧提交 `3bf0f90` 及源码摘要。四组完整图比较全部计划和文件字节，覆盖不同层型、shared expert、批次、分块、上下文、零 Token 成员、来源和身份。旧流程发生三次完整合并，新流程发生两次：构建一次、重构校验一次。

四块资源对照保留 128 rank、1024 请求及原 bundle：

| 指标 | 旧构图后通用导出 | 一次调用完成构图与导出 |
|---|---:|---:|
| 构图、完整校验及导出 | 39.340 s | 33.265 s |
| 进程总时间 | 40.986 s | 34.847 s |
| 采样峰值 RSS | 847,847,424 B | 883,277,824 B |
| 完整合并次数 | 3 | 2 |
| 核对文件/总字节 | 131 / 322,598,087 B | 相同 |

所有文件大小及 SHA-256 一致。该次耗时下降约 15.4%，没有观察到内存下降，不改变仿真时间。

完整 61 块目标于 896.514 s 完成构图校验并开始写出，900.059 s 触及时间上限。峰值 RSS 为 33,902,088,192 B，约 31.57 GiB；外层退出 1、worker -15，无残留进程。只留下六个可能包含截断的 Chakra 文件及 schema，没有 plan.json 或成功清单。目标 C++ 加载和网络执行在该次测量中未开始；开始写文件不能作为完成导出的证据。后续序列化和加载工作见[资源工程记录](../m10_resource_efficiency/README.md)。

19 项相关单元检查通过，包含输入隔离、非法计划拒绝、写出失败不生成成功清单。小型完整服务、61 块小 EP、穿插 dense/无 shared expert 和损坏 JSON/Chakra 拒绝通过；15 个历史小图文件逐字节一致，依赖和受保护文件未变。开发时旧模块缺少 `__file__`、提前读取未完成测量结果的两次检查失败已保留；修正测试后没有改变生产规则或测量输入。

## 复现与证据

```bash
PYTHONPATH=python:tests/unit python -m unittest \
  full_model_export_test full_model_storage_test full_model_test \
  full_round_probe_test resource_budget_test
```

`scripts/probe_full_model_export.py baseline|closed --output-dir ...` 需要固定旧提交及原首轮 bundle，须由 `moe_ub_cosim.host_probe` 包裹。四块对照限 300 s、RSS 4 GiB、地址空间 6 GiB、产物 2 GiB；完整首轮沿用[预检预算](../m10_full_round_preflight/README.md)。输出目录必须不存在。

[result.json](result.json) 保存输入、阶段、资源和回归结果；命令与日志位于 `runs/v03-full-model-export/`。完整历史表见[原说明](https://github.com/baoy21/moe-ub-cosim/blob/e20d42a2bcf0b2ac2529b8641a9492638ffc786f/fixtures/m10_full_model_export/README.md)。后续完整首轮执行与用户交付已在[最终验收](../m10_research_slice/README.md)记录。
