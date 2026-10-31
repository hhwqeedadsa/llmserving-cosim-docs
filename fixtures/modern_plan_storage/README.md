# 现代计划压缩保存验收

公开操作见[现代计划](../../examples/modern_deepep_partial_order/README.md#可选压缩存储)，要求见[规范](../../docs/tasks/modern_plan_storage_spec.md)。本增量仅保存和检查无时间部分顺序，不执行网络、GPU、动态slot或原生回调。

人工EP4/E8/两调用公开流程从新目录运行，完整解压与flat规范JSON一致，正常及暂扣零expert-count事件的两种闭包结果相同；默认三个输出与改动前原件逐字节一致。新单元检查覆盖压缩流、声明边界、固定文件名、预算和重算hash后的内容篡改，旧现代计划/事件/窗口回归另行通过。实际摘要见[验证](verification.json)。

```bash
PYTHONPATH=python python3 -B tests/integration/check_modern_plan_storage.py --output runs/my-modern-storage-check
```

命令不依赖本机旧运行产物；`--baseline`只是本轮验证改动前默认字节的可选参数。真实EP32容量研究独立保存，不能用本例证明已完成其网络执行或性能校准。
