# 持久RDMA物理网卡绑定验收

本增量让持久流、事务和现代功能入口在同一plane内使用多张实际网卡，能够表达独立rail和共享Agg的竞争。源QP按真实接口拥有的本地地址绑定NIC，别名目的路由只允许从指定NIC进入GPU；ACK/CNP和事务回复也经过这些设备。共享交换机、MMU和仿真时钟只存在一份。规范见[事前范围与预算](../../docs/tasks/physical_nic_binding_spec.md)，公开使用见[共享rail示例](../../examples/scaleout_multirail_native/README.md)。

原生补丁`47f8fad`基于`68a07f7`，ASTRA指针`0c51d08`，LLMServingSim指针`94f3b00`。全部仅本地保存，没有上游升级、下载或push。新增地址不含任务/专家标签，不以QP hash凑网卡；普通SEND/RECV保留原域。同GPU/plane一个NIC仍使用原地址。

## 实际功能结果

新目录公开`rdma-build`完整编译446项任务、5个程序，编译279.556秒，使用新头文件和新动态库。20项原生对象检查覆盖错接口、重复IP、晚注册、删除QP后不能重新注册、发送/接收QP和清表后的绑定。为查看零字节实际QP的队列条目，对象测试先关闭本地设备，避免原生同步调度器立即清除空条目；没有运行Simulator或发包，也不代表实现了生产NIC故障恢复。

纯路由10项测试包括64个四交换机子图、两个方向、四种物理NIC配对，共512个组合，与独立简单路径枚举一致；另保留旧最短首跳集合及边顺序核对。153项Scale-Out和52项定向回归通过，计数包含复用，不称205项新增测试。

| 实际条件 | 结果 | 能支持的结论 |
|---|---|---|
| 修改前/后单NIC | 完整result除加载库路径外一致 | 包、时刻、队列及回调在这个小例中兼容 |
| 同plane独立rail，2条流 | 全部完成，66源包、792链路发送 | 两条物理绑定路径可执行 |
| 共享Agg拥塞，4条流 | 全部完成，132源包、53个实际CNP、1,902链路发送 | 不同源NIC在同一交换出口排队，反馈真实返回 |
| 同源两NIC到同远端NIC，MRT1事务 | 6操作、4原子回复，最终原子值10 | 请求/回复绑定及实际字节/内存副作用一致 |
| 共享Agg现代两调用 | 2rank、322应用事件全部完成，46源包 | 合成完整操作数、窗口/保护/回调的功能关系正确 |
| 有替代路径的交换机边故障 | 3,500ns路由更新、4条流完成 | 别名路由参与更新且绑定不迁移；本次选路没有丢包，不能据此声称恢复收益 |
| 断开网卡1所在ToR唯一上联 | 6次到达丢弃、93次本地丢弃；3流完成、1流在560us有限失败 | 同GPU健康NIC2可完成，失败流99个源包均从NIC1发送，没有免费换卡 |

共8次网络（含旧/新兼容两次），未重复成功网络。`checks.py`从保存的包头和物理边重建每次TX/RX、正反向NIC、目的NIC最短路、FIFO、串行/传播、端口字节及队列面积/峰值；按真实接收/ACK核对工作回调，解码MRT1核对目标字节、原子回复与最终内存。现代层复用独立`fixtures/modern_native/audit.py`核对实际字节、窗口和完整事件关系，不调用现代控制器推进逻辑。10类证据篡改均拒绝。

错误NIC配对经stream/transaction/modern三个公开CLI均在创建运行输出和启动marker前拒绝；marker只用于负向启动检查，不执行网络。13项固定依赖核对及离线恢复结果分别见同目录版本化收据。

## 复核与恢复

原始输入、结果、失败和全部命令在`runs/physical_nic_binding_20261027/`。公开示例的三个JSON与共享拥塞条件逐字节相同。功能检查代码、摘要和依赖身份随Git保存；完整原件及实际5程序/8库保存在该目录的`checkpoint.tar.gz`，SHA-256见`checkpoint.tar.receipt.json`和本目录`recovery.json`。

只读原件复核（不执行网络）：

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m fixtures.physical_nic_binding.verify --stage runs/physical_nic_binding_20261027
```

使用新目录恢复，先核对预期SHA-256；代码只允许有界普通文件并逐成员核hash，不使用不受约束的tar解压。恢复从复制源码和空cwd重新计算验收，比较`verification.json/tampering.json`逐字节一致，并重复10项路由方法，不构建、不执行网络。依赖bundle为本地增量Git对象，需要记录的基线提交；不能声称这些新提交已能从远端取回。

```bash
PYTHONPATH=python:. PYTHONDONTWRITEBYTECODE=1 python3 -B \
  -m fixtures.physical_nic_binding.checkpoint restore \
  --archive runs/physical_nic_binding_20261027/checkpoint.tar.gz \
  --output runs/physical_nic_binding_20261027/restored-copy
```

原Git身份未配置、一次未保存源码的构建准入拒绝、对象测试缺少显式applications动态库链接、审计器首版错误合并ACK与数据优先级、父仓库指针保存前的依赖检查失败均保留；相应更正不改变仿真工作量。分区退出非零是预期功能结果。没有增加任何预算或重跑成功网络。

累计1800受监控秒/2GiB，配置60秒/编译300秒、两并发，普通命令60秒、8GiB可用空间保留；最终核算见`closeout.json`。功能完成不证明新拥塞算法、硬件精度、完整LLMServingSim服务、数值专家计算或千卡现代应用。两个论文候选与整体研究目标仍进行中。
