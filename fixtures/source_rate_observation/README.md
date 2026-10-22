# 源端速率及有效参数观测验收

本平台增量解决“发生降速后只能按源码反推、结果没有完整默认参数清单”的诊断缺口。Fabric从实际初始化对象读取有效属性；持久字节入口可在已有发送和ACK回调内同步读取当前状态。没有新增仿真事件，没有改变网络参数、数据包、信用释放或任务依赖。用户配置和字段见[源端速率观测](../../examples/scaleout_rdma_streams/README.md#可选的源端速率观测)。

## 实际结果

| 人工功能场景 | 源数据包 / ACK对 | 速率样本 | 带CNP的ACK | 发送时最低QP速率 | 完成时间 |
|---|---:|---:|---:|---:|---:|
| 默认6工作、2持久连接 | 35 / 35 | 105 | 0 | 100 Gb/s | 63,050 ns |
| 3源到同一目的、每连接64KiB后按ACK追加4KiB、ECN阈值1000/2000B | 213 / 213 | 639 | 167 | 6.25 Gb/s | 41,338 ns |

两行是**不同人工负载**，完成时间不作优劣比较；ECN阈值为本次功能检查声明值，没有硬件或生产配置依据。每行分别对照`29f55d4`阶段旧二进制、新版关闭采样、新版开启采样。仅去掉新增`rdma_parameters/source_rate_capture`，其余整个结果对象完全一致，包括每包UID/路径、队列事件/积分、端口计数及完成时间。旧二进制、旧动态库及输出目录内容hash保持不变。

独立检查按实际物理设备TX/RX事件核对248个源包、248对ACK及反馈CNP位；核对3次首次CNP状态转换、累计确认前缀、发送时序和最终序号。采样窗口精确等于全量样本的过滤结果，重复结果完全相同。容量溢出明确失败，非法布尔值在CLI及绕过CLI的原生入口均拒绝；失败没有成功结果文件。详见[采样验收JSON](verification.json)。

15项输入单元检查通过，其中新增的测试方法覆盖时间窗、类型、未知字段和采样容量。原持久流程7条公开命令、事务流程8条公开命令均通过（分别含2次预期拒绝）。旧消息probe的4条消息最后ACK为312,688ns，该入口仍观察至5,000,000ns才报告，不能把顶层finish_ns当消息耗时；在线前端的一层两轮人工输入完成160条消息，54,243ns，完整原有网络结果、ASTRA节点日志一致，前端仅归一化运行目录后相同。见[probe核对](probe.json)、[在线服务核对](service.json)及[完整维护记录](maintenance.json)。

## 复核与复现

在包含固定依赖、已有工具的环境中，正常构建仍用公开`scaleout rdma-build`；没有新增依赖。新旧可执行文件须分别保留，不能用新文件覆盖旧文件后作比较。使用新目录执行：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python3 tests/integration/check_source_rate_workflow.py \
  --backend /absolute/new/ns3.42-moe-rdma-stream-probe \
  --reference-backend /absolute/old/ns3.42-moe-rdma-stream-probe \
  --output /absolute/new-check-directory
```

另用包含前端依赖的Python和已有protoc执行：

```bash
PYTHONPATH=python PYTHONDONTWRITEBYTECODE=1 python tests/integration/check_rdma_parameters_workflow.py \
  --backend-dir /absolute/new/binaries --reference-dir /absolute/old/binaries \
  --protoc /absolute/path/to/protoc --output /absolute/new-check-directory
```

默认同时验收probe/service；`--mode probe`或`--mode service`可单独检查。所有输入都由版本化小例生成，不依赖真实路由数据或旧研究产物。

本次为保护旧绝对路径及其历史hash，从已有CMake命令提取平台编译/链接参数，在新内存盘目录编译受影响对象，复用原只读ns-3库；四个入口均构建成功。一次隔离链接保留旧cwd相对库路径而失败，修正为绝对路径后仅补链接。另一次测试误用`decode_steps=1`，被平台按最低2轮拒绝；修正测试后只完成服务对照，没有重复已通过的probe。两次错误和日志保留，没有提高预算、修改网络来迁就检查。见[构建记录](build.json)。

第一次打包把仍在写入的监控样本纳入快照，逐文件hash检查拒绝了该归档。保留失败包、日志及hash，排除活跃监控和旧生成包后重新打包；没有重跑网络。最终643项归档全部逐hash核验通过，约20.44MiB，新任务累计产物约106.14MiB，包含失败包，仍在128MiB上限内。最终证据核对见[final-check.json](final-check.json)。

每阶段60秒、RSS1GiB、地址2GiB；新产物累计128MiB、已监控互斥阶段300秒、内存盘预留8GiB。含打包的已监控互斥执行约97.43秒，最高采样组RSS788,733,952B。13项依赖检查通过；没有安装、下载、修改固定依赖或重跑真实Prefill失败场景。完整原始日志、二进制和共享库检查点见[归档清单](archive.json)。根卷低于8GiB预留，归档仅在`/dev/shm`，**易失且尚无持久备份**。

## 研究边界

本次直接采样验证发生在两个小型人工字节场景，不能替代此前真实Prefill的参数校准，也没有证明新的优化收益。默认100Mb/s最低速率、1500us恢复周期仍未校准；这个增量只把其生效值明确保存。记录不是完整的定时器状态转移史，未连接现代完整DeepEP、GPU可见性或生产推理时序。没有扩大千卡/完整模型的已验收范围。

下一步研究应先取得匹配系统的参数和时序依据，再用独立请求检查拥塞反馈解释是否成立，并检验是否存在超出链路服务量、已有放置与拥塞控制基线的贡献。不能为了使旧负载更快而调默认值，也不能把本次平台验收作为A类论文目标完成。
