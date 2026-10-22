# 原生接收窗口等待与消费验收

本增量补齐原生MRT1功能程序的一项缺口：后续操作能够依据提交GPU本地实际收到的值继续，而非必须引用全局操作完成事实。平台代码位于`backend/rdma/remote_memory.hh`、`remote_transactions.cc`及`transaction_probe.cc`；用户接口和两轮输入见[事务示例](../../examples/scaleout_rdma_transactions/README.md#根据实际收到的值推进下一轮)。没有执行现代DeepEP、CUDA或GIN provider，不证明通信性能或论文贡献。

## 结果及边界

- [verification.json](verification.json)：8条公开CLI；正常两轮10事务/14源数据包/560B载体有效负载。独立struct解码实际发出包，按远端应用和本地消费的共同order重放内存；核对6项条件及4次本地继续，最终计数归零。原子返回值仍通过实际网络返回。
- 条件全部满足前不消费。少一个原子增量时计数为1；数据错误时计数为2但另一项不满足。二者均完成最初3个事务，7个后续操作保持等待，不提前清零、不发未来包；失败结果与日志保留。
- 目标GPU可以在生产者源端完成之前消费已到达数据；两轮反馈都来自真实通知包。全部显式after只等待同一提交GPU的本地完成，没有跨GPU借用全局applied事实。
- `ge`对照收到4时满足阈值2，随后消费到0；它改变工作值而不改变请求长度，不是性能优化。正常输入重复结果逐字节一致，应用id统一改名不影响线上字节及时间。
- [regression.json](regression.json)：原事务8条CLI回归，含跨包、深度2、重复与拒绝。新功能是可选字段，旧输入继续可用。
- [maintenance.json](maintenance.json)：严格C++17核心、6项输入单元检查、增量构建、资源、来源hash和命令。核心新增本地范围/权限与消费后旧原子重放检查；后者不能当成网络丢包恢复验收。
- [archive.json](archive.json)：本地检查点保存本轮原始输入/输出/失败日志及源码，逐文件hash复核；不含二进制或依赖源码，完整构建仍使用固定依赖。

本地LE64读写与eq/ge等待是明确的功能应用行为，没有GPU内存序、轮询时间、部分DMA可见或物理NIC服务模型。局部消费不证明一般应用协议无竞态；源buffer、epoch、self-peer GIN及现代信号域均未由此接入。现代窗口模块仍是独立的调用者事件检查，不能将本增量写成现代端到端联合仿真。

## 复核

在仓库根运行，输出目录须不存在。BACKEND是公开`scaleout rdma-build`输出清单中的transaction_probe，不使用UB程序：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_local_memory_wait_workflow.py \
  --backend /ABS/BACKEND --output /dev/shm/my-local-wait-check
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p scaleout_transactions_test.py
c++ -std=c++17 -Wall -Wextra -Werror -pedantic -Ibackend \
  tests/unit/remote_memory_test.cc -o /dev/shm/my-memory-test
/dev/shm/my-memory-test
```

使用仓库host_probe约束每命令60秒、RSS1GiB、地址2GiB、新产物64MiB/累计128MiB和卷预留8GiB。完整实际命令保存在维护记录。检查不安装工具、不改变固定依赖，也不触发千卡或全模型运行。
