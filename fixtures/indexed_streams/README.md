# 持久字节路径的索引与显式计数验收

本增量减少宿主扫描，并使连接/工作数量可明确配置。它仍是原生字节功能模型，不是现代GIN性能复现，不证明千卡或论文贡献。用户输入及预算见[示例](../../examples/scaleout_rdma_streams/README.md)。

改前16连接/64工作同时存在于Python、probe、传输对象和每连接工作队列构造中，属于人工功能入口保护，不是协议极限。v1和事务入口继续使用原值；`stream-work/2`必须声明`max_streams`（1—128）、`max_work`（1—8192）。实际完成仍受原有时间、内存、字节、观测限制，不能把允许填写的最大数当成全部组合已验收。

连接查找从全扫描改为唯一线端端口索引；发布、接收和ACK从历史工作扫描改为累计前缀；发送按序号二分定位已发布区间，保留NACK回退能力。应用依赖按真实回调更新后继，输入顺序是同次就绪的平局规则。没有修改网络时钟、数据包、ECMP、ACK信用或依赖完成来源。

在全新工作目录执行15条公开CLI及1次直接二进制拒绝检查，结果见[verification.json](verification.json)：

| 检查 | 结果 |
|---|---|
| 同输入新旧完整结果 | 12连接/48工作/163源包/137,873B，`result.json`逐字节相同 |
| 后列工作先就绪 | 2连接/6工作/35包；同连接work2在work1前提交，新旧完整结果相同 |
| 明确扩展 | 8GPU/24连接/192工作/8轮，563,568B、672个实际源数据包，完成104,228ns |
| 因果及信用 | 独立从输入/MTU枚举包，核对前驱最大回调时刻、累计区间和深度1/2/4的ACK归还，扇入等待不偷发 |
| 重复 | 原小例及扩展的完整结果分别逐字节相同 |
| 拒绝 | 前向依赖、错误NIC、声明连接/工作不足，以及绕开CLI的原生数量超限；共5类预期失败 |
| 受影响回归 | 远端事务、本地接收条件、队列前缀各8条公开CLI通过，共24条 |
| 输入检查 | 7项单元检查，包括v1不自动升级、v2类型/范围/字段、端点数与连接计数分离 |

两受影响目标分别增量构建，单命令60秒/RSS1GiB/地址2GiB/产物64MiB。实际构建约11.64/17.74秒，采样峰值约550.3/552.2MiB；最终主流程约9.70秒、409.6MiB，三组事务回归合计约13.76秒、420.0MiB。另冻结`ba02125`的三个stream源文件重编对照，确认与最初保存的二进制hash相同；共享Fabric/队列源文件也与该提交一致，消除了旧可执行文件来源疑点。构建产物范围含已登记的目标对象目录和可执行目录；全部新产物累计限制128MiB、卷预留8GiB。完整日志及旧二进制放`/dev/shm/moe-stream-indexed-20261022/`；Git保留小型摘要及命令，见[maintenance.json](maintenance.json)。未下载或改动锁定依赖。

复现普通流程无需旧产物；差异核查可额外传入一个按改前提交`ba02125`构建的原生stream-probe：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests/unit -p scaleout_streams_test.py -v
PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_persistent_stream_workflow.py \
  --backend /absolute/new/native-stream-probe \
  --reference-backend /absolute/pre-index/native-stream-probe \
  --output /absolute/new-check-directory
```

差异核查要求两版本使用同一套固定原生动态库，否则`loaded_libraries`的路径本身会使完整文件不同。未加`--reference-backend`时执行原有7条CLI；扩展检查需要该参数。源码、构建及运行身份保存在摘要中。没有受控宿主计时对照，不能给出加速比；本轮没有主动丢包/NACK实验，保留代码路径不等于已校验故障恢复。

原始运行目录位于易失tmpfs，另在本机磁盘保存全部578文件的检查点并逐文件核对hash，见[archive.json](archive.json)。它是本地持久副本，不是异机备份；代码及小型摘要另由Git保存。
