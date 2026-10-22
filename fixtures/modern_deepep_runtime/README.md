# 现代direct事件核心验收

对象是[事件核心](../../python/moe_ub_cosim/modern_deepep_runtime.py)，公开用法见[示例](../../examples/modern_deepep_partial_order/README.md)。仅核验无时间、调用者提供的事件顺序，不运行CUDA、队列、provider或ns-3。人工测试完成回调不能作为研究中的网络执行。

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 timeout 60s python3 -m unittest discover \
  -s tests/unit -p '*deepep*test.py' -v
PYTHONDONTWRITEBYTECODE=1 timeout 60s python3 tests/integration/check_modern_runtime_workflow.py \
  --output runs/my-modern-runtime-workflow
```

工作目录为仓库根，output为新目录。集成检查在新cwd执行10条公开命令，包含4类预期拒绝：提前分配、重复事件、未知事件、缺少前驱的完成。单子进程≤15秒/地址2GiB/CPU30秒，合计≤60秒、RSS≤1GiB、输出≤8MiB，卷预留8GiB。命令和stdout/stderr留在输出目录，失败保留且不自动重跑或扩限。

独立oracle读取公开plan，使用自身逐事件完成集合、整数槽计数和通道分区表达预期，不导入被测runtime；以确定性的测试顺序生成1,076事件及16次分配，逐项回查日志及16个Combine绑定。重复结果和迁移结果逐字节相同，见[验收摘要](verification.json)。测试顺序不是GPU或provider调度策略，也不能据其推断运行中的连接利用率。

7项单元检查覆盖延迟token及同角色后继、运行时slot双射、两种合法分配顺序改变连接、同Combine通道先归约后提交再处理下一序号、同token全部目标slot分配后的warp同步、本地完成与远端可见独立、零块等待、最终信号尾部、空输入rank和跨调用隔离。输入及查询返回值都不能修改内部状态。

源码固定范围仍与现代计划一致。新增Dispatch同步依据是固定`dispatch.cuh`在`atomicAdd`分配后、TMA等待和put前的`__syncwarp()`；Combine顺序来自固定`combine.cuh`按接收序号连续循环。原符号DAG只含必要依赖，未表达这项Dispatch集合边界；事件核心执行该额外约束。平台未改变历史固定提交及研究结果的语义。

这不是任意并发的形式化证明。队列反压、doorbell、源buffer寿命、持久物理QP、原子恰好应用一次、跨NIC可见性和共同ns-3时钟均待接入。通过本检查不能证明性能、硬件准确或论文贡献。
