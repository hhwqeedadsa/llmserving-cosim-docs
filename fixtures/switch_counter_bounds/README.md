# 交换机对象边界补验收

本轮补齐[原轮存储验收](../switch_counter_storage/README.md)中因遗漏 applications 链接库而未执行的八项原生对象检查。范围及预算见[独立规范](../../docs/tasks/switch_counter_bounds_spec.md)。

同一源码、同一组已登记 hash 的八个原生库完成一次独立编译。port/legacy 正常结束；zero/too_many/twice 必须以 SIGABRT 和初始化诊断结束，bad_in/bad_out/bad_priority 必须以 SIGABRT 和越界诊断结束。八项均通过。仅复制小二进制及八库，在空工作目录再检查同八项；动态加载器记录证明使用搬迁后的八库，逻辑结果一致。

[核验](verification.json)保存源码、二进制和动态库 hash、逐项退出与诊断；[收尾](closeout.json)保存资源与边界。实际命令、编译/对象日志和监控原件位于 `runs/switch_counter_bounds_20261027/` 及相邻 `-monitor/`。含嵌套监控保守计 4.621 秒，约 16.24 MiB 分配产物，保持 180 秒/64 MiB。无 ns-3 构建、网络执行、下载、GPU、依赖修改或推送。

原轮 `acceptance_complete=false` 和链接失败原件保持不变；本补验收与原轮合起来完成存储功能验收。闭包整理首次因漏设 `PYTHONPATH` 在任何写入前失败，修正环境后仅离线保存摘要，没有重新编译或执行对象。本功能是精确存储工程，不是两项论文创新的证据。

如需在另一次有独立预算的验收中复现，入口为 `PYTHONPATH=python python3 -B fixtures/switch_counter_bounds/check.py --repo "$PWD" --output <不存在的新目录>`；需先具备原轮公开构建及其身份清单，本次额度已经关闭。
