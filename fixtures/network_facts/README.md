# 被动网络事实验收

从仓库根目录使用项目 Python 环境执行：

```bash
python tests/integration/check_network_facts.py --backend build/default/output/cosim/ns3.44-cosim-service --output runs/network-facts-check
```

输出必须是新目录。测试使用合成三 rank 输入，检查实际信用消耗/归还、记录开关的执行与报文等价、半开边界、记录超限失败、私有导出、切片重放以及不依赖历史运行的公开示例。测试用 64 单元信用和 32 单元控制归还阈值；目标研究的 6553/1024 基线不变。独立 `cosim-network-facts-test` 验证 161 字节报文在 160 字节信用单元下需要两个单元，剩一个单元仍阻塞，观察不弹出队首。

[验收摘要](validation.json) 保存实际命令和失败原因入口。支持的用户操作见[原生小例](../../examples/native_first_round/README.md)，数据语义见[记录格式](../../docs/record_storage.md#网络机制事实)。
