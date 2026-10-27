# 修复公开原生构建的版本身份

对应[独立规范](../../docs/tasks/native_build_identity_spec.md)，起点`b27f008`、规范`d344fa0`。`scaleout rdma-build`过去仍固定`6088174`，与实际锁定的`68a07f7`不符，会拒绝正确的依赖检出。修复统一读取锁文件，并核对ASTRA父gitlink、实际HEAD、已跟踪源码状态和resume身份；没有修改或升级依赖。纯输入检查不会因导入模块而运行Git或读取锁文件。

普通probe、持久流、事务与事务会话的来源字段也移除了旧常量。`native_commit_role`明确该字段只是源码预期；本次实际程序及所加载8个原生库的hash另行保存，不能由源码字段推断任意二进制的构建身份。

## 实际验收

- 全新目录通过公开`rdma-build`完成446项编译/链接任务，生成probe、持久流、事务、会话和服务5个程序；没有恢复旧对象。编译279.538秒，低于300秒/固定两并发限制，全部输出与UB隔离。
- 一次公开topology/rdma-probe完整执行4条消息，独立核对6,000B源/目的连续数据、7个源数据包、7个ACK、14条完整包路径、84次物理发送/84次接收以及所有端口字节，没有捕获遗漏。这里只执行普通功能探针，没有完整MoE应用或LLMServingSim服务。
- 11个新增方法使用真实临时父/子Git仓库验证身份错误；153项Scale-Out回归、49项定向检查通过。运行计数包含复用用例与重复检查，不称202个独立新用例。
- 按203成员清单核hash恢复后，在复制源码和空cwd重新离线核对，验收JSON逐字节一致，11项身份检查通过；没有第二次构建或网络执行。
- 13个依赖检出一致且不变。初次回归漏改三个旧常量导入、首次probe命令误用`--protocol`的失败记录均保留；后者在参数解析时结束，没有启动网络。没有为成功结果反复运行网络或提高预算。

详细证据：[网络/程序核验](verification.json)、[复制源码恢复](recovery.json)、[预算收尾](closeout.json)。原始命令、stdout/stderr、首次失败、全新构建和运行时位于`runs/native_build_identity_20261027/`。预算900受监控秒/1GiB，统计包含嵌套监控时段，不能解释为宿主性能基准。

## 离线复核

持久包`runs/native_build_identity_20261027/checkpoint.tar.gz`包含本阶段Python源码、输入、消息/包原件、构建收据及同主机ABI的5程序/8库；不包含整套依赖源码、构建对象和CMake目录，因此恢复不是从零重建证明。实际从新目录编译的证据在原构建记录中。运行以下命令只恢复并重算检查，输出目录须不存在：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B -m fixtures.native_build_identity.checkpoint restore \
  --archive runs/native_build_identity_20261027/checkpoint.tar.gz \
  --output /dev/shm/native-build-identity-review
```

新的完整功能验收先通过公开`rdma-build`生成`backend.json`，再通过公开topology/rdma-probe运行本阶段输入；工具不在PATH时，使用`--cmake`、`--ninja`、`--protobuf-prefix`指向已有安装。实际命令见运行目录的`public-build-monitor/probe.json`、`topology-monitor/probe.json`和`network-corrected-monitor/probe.json`，不要把归档运行时当作新构建。

本修复是可复现构建的基础，不改变包、选路、计时或协议，不解除[同plane多NIC限制](../../docs/reviews/多rail网络建模边界与原生绑定改进_20261027.md)，也不证明硬件校准或两项论文创新。下一阶段需要独立实现物理NIC地址、发送QP及反向ACK/CNP绑定，然后才开展共享Agg/多rail的性能研究。
