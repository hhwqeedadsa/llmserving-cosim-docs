# 独立任务的备用门槛

[执行前收据](pre-network.json)记录70项不同CPU检查、六份旧网络的只读兼容核验及自包含128端点Clos输入准备。实现与格式见[逐任务门槛](../../docs/whole_call.md#为独立任务设置不同门槛)，公开操作见[双任务示例](../../examples/scaleout_multicall/README.md)。本阶段未修改原生程序或依赖，复用同一套ns-3。

新增检查覆盖：按最早期限设置唯一原生定时器；call声明顺序不改变提交顺序；同刻启动所有到期任务；只完成自己的请求与释放自己的备用KV；一个任务不能借用另一任务的门槛；旧统一门槛兼容；真实LLMServingSim的独立held队列与实际释放；非法全局/逐call混用、无效时间和模拟停止边界被拒绝。

执行前证据不证明共享网络影响或优化收益。网络验收与正式研究另行记录，保持图、阶段门限、接收/源确认和后台清理。

[实际网络验收](post-network.json)使用已保存的两个128端点Clos多任务结果，各迁移到新目录，通过公开离线检查，不重新执行网络。每份结果拒绝12类篡改，共24项：独立门槛、交换备用组、缺失call、唯一原生定时器、伪造健康任务备用执行/ITL/提前完成、借用其他call门槛、各call实际KV释放，以及在更新引用SHA后将未知修订来源升级为published的无依据声明。原件与恢复后的迁移副本逐文件SHA一致。

可通过 `PYTHONPATH=python .venv/frontend/bin/python -B tests/integration/check_multicall_run.py --run <已保存的双任务结果目录> --output <不存在的新目录>` 复核；检查只读取并复制结果，不构建、不联网、不执行ns-3。功能验收不要求备用对健康任务产生干扰，研究结论见[双任务结果](../../experiments/multicall_externality/results/20261030/README.md)。
